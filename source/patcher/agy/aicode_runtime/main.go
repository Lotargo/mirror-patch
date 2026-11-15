// agy compatibility launcher. OAuth, licensing and response decoding stay in agy.
package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const upstream = "https://aicode.googleapis.com"
const bodyLimit = 16 << 20

// Set by the reproducible installer when compiling the launcher.
var nativeSHA256 string
var hubEnabled string

type experience struct {
	Name  string `json:"name"`
	Model *struct {
		Name        string `json:"name"`
		ID          string `json:"modelId"`
		DisplayName string `json:"displayName"`
	} `json:"model"`
}

type config struct {
	Experiences []experience `json:"availableExperiences"`
	Surface     struct {
		Agent struct {
			Names []string `json:"experienceNames"`
		} `json:"agentExperiences"`
		Tiered struct {
			Names []string `json:"experienceNames"`
		} `json:"tieredFlashExperiences"`
	} `json:"agySurfaceConfig"`
}

func (c config) candidates() []experience {
	byName := map[string]experience{}
	for _, e := range c.Experiences {
		if e.Model != nil && (e.Model.Name != "" || e.Model.ID != "") && e.Name != "" {
			byName[e.Name] = e
		}
	}
	var result []experience
	seen := map[string]bool{}
	for _, name := range append(append([]string{}, c.Surface.Agent.Names...), c.Surface.Tiered.Names...) {
		if e, ok := byName[name]; ok && !seen[name] {
			result = append(result, e)
			seen[name] = true
		}
	}
	return result
}

func (c config) selectExperience(requested string, explicit bool) (string, error) {
	choices := c.candidates()
	if len(choices) == 0 {
		return "", errors.New("fetchConfig contains no enabled agent experiences with a model")
	}
	want := strings.TrimPrefix(requested, "models/")
	for _, e := range choices {
		if e.Name == want {
			return e.Name, nil
		}
	}
	var matches []string
	for _, e := range choices {
		if want != "" && (strings.EqualFold(want, e.Model.ID) || strings.EqualFold(want, strings.TrimPrefix(e.Model.Name, "models/")) || strings.EqualFold(want, e.Model.DisplayName)) {
			matches = append(matches, e.Name)
		}
	}
	if len(matches) == 1 {
		return matches[0], nil
	}
	if len(matches) > 1 {
		return "", fmt.Errorf("model is ambiguous; select an experience: %s", strings.Join(matches, ", "))
	}
	if explicit {
		return "", fmt.Errorf("selected model %q is absent from fetchConfig; available experiences: %s", want, choiceNames(choices))
	}
	// Native default may refer to a removed model. Follow server UI ordering.
	return choices[0].Name, nil
}

func choiceNames(choices []experience) string {
	names := make([]string, len(choices))
	for i, e := range choices {
		names[i] = e.Name
	}
	return strings.Join(names, ", ")
}

type cachedConfig struct {
	value   config
	expires time.Time
}
type adapter struct {
	client      *http.Client
	nonce       string
	explicit    bool
	mu          sync.Mutex
	configs     map[[32]byte]cachedConfig
	onSelection func(string)
}

func newAdapter(nonce string, explicit bool) *adapter {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.ResponseHeaderTimeout = 45 * time.Second
	return &adapter{nonce: nonce, explicit: explicit, configs: map[[32]byte]cachedConfig{},
		client: &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
}

func readBounded(r io.Reader) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, bodyLimit+1))
	if len(b) > bodyLimit {
		return nil, errors.New("request or config exceeds size limit")
	}
	return b, err
}

func copyHeaders(dst, src http.Header) {
	for k, vv := range src {
		switch strings.ToLower(k) {
		case "connection", "proxy-connection", "proxy-authorization", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade", "content-length", "accept-encoding":
			continue
		}
		dst[k] = append([]string(nil), vv...)
	}
}

func (a *adapter) send(ctx context.Context, method, path string, h http.Header, body []byte) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, method, upstream+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	copyHeaders(req.Header, h)
	return a.client.Do(req)
}

func decodeConfig(b []byte) (config, error) {
	var c config
	if err := json.Unmarshal(b, &c); err != nil {
		return c, errors.New("invalid fetchConfig JSON")
	}
	if len(c.candidates()) == 0 {
		return c, errors.New("fetchConfig has no enabled agent model")
	}
	return c, nil
}

func (a *adapter) getConfig(ctx context.Context, headers http.Header) (config, error) {
	key := sha256.Sum256([]byte(headers.Get("Authorization")))
	a.mu.Lock()
	defer a.mu.Unlock()
	if entry, ok := a.configs[key]; ok && time.Now().Before(entry.expires) {
		return entry.value, nil
	}
	resp, err := a.send(ctx, "GET", "/v1alpha:fetchConfig", headers, nil)
	if err != nil {
		return config{}, errors.New("fetchConfig transport failed")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return config{}, fmt.Errorf("fetchConfig returned HTTP %d", resp.StatusCode)
	}
	b, err := readBounded(resp.Body)
	if err != nil {
		return config{}, err
	}
	c, err := decodeConfig(b)
	if err != nil {
		return config{}, err
	}
	a.configs[key] = cachedConfig{c, time.Now().Add(5 * time.Minute)}
	return c, nil
}

func patchRequest(b []byte, c config, explicit bool) ([]byte, string, error) {
	var request map[string]json.RawMessage
	if err := json.Unmarshal(b, &request); err != nil || request == nil {
		return nil, "", errors.New("invalid generation JSON")
	}
	var code map[string]json.RawMessage
	if err := json.Unmarshal(request["aicode"], &code); err != nil || code == nil {
		return nil, "", errors.New("missing aicode experience in native request")
	}
	var requested string
	if err := json.Unmarshal(code["experience"], &requested); err != nil {
		return nil, "", errors.New("missing aicode.experience in native request")
	}
	name, err := c.selectExperience(requested, explicit)
	if err != nil {
		return nil, "", err
	}
	code["experience"], _ = json.Marshal(name)
	request["aicode"], _ = json.Marshal(code)
	result, err := json.Marshal(request)
	return result, name, err
}

func fail(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"code": status, "status": "FAILED_PRECONDITION", "message": "agy compatibility: " + message}})
}

type flushingWriter struct{ http.ResponseWriter }

func (w flushingWriter) Write(p []byte) (int, error) {
	n, err := w.ResponseWriter.Write(p)
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
	return n, err
}

func (a *adapter) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// A random per-process path prevents other local pages from using this adapter.
	path := r.URL.Path
	prefix := "/" + a.nonce
	if len(path) < len(prefix) || subtle.ConstantTimeCompare([]byte(path[:len(prefix)]), []byte(prefix)) != 1 {
		http.NotFound(w, r)
		return
	}
	path = strings.TrimPrefix(path, prefix)
	method := map[string]string{"/v1alpha:fetchConfig": "GET", "/v1alpha:fetchLicenses": "GET", "/v1alpha:fetchQuotaStatus": "GET", "/v1alpha:streamGenerateContent": "POST", "/v1alpha:generateContent": "POST"}[path]
	queryOK := r.URL.RawQuery == "" || (path == "/v1alpha:streamGenerateContent" && r.URL.RawQuery == "alt=sse")
	if method == "" || r.Method != method || !queryOK {
		http.NotFound(w, r)
		return
	}
	if r.Header.Get("Authorization") == "" {
		fail(w, 401, "missing native OAuth authorization")
		return
	}
	b, err := readBounded(r.Body)
	if err != nil {
		fail(w, 413, "request exceeds size limit")
		return
	}
	if strings.HasSuffix(path, "GenerateContent") || strings.HasSuffix(path, ":generateContent") {
		c, err := a.getConfig(r.Context(), r.Header)
		if err != nil {
			fail(w, 502, err.Error())
			return
		}
		var selected string
		b, selected, err = patchRequest(b, c, a.explicit)
		if err != nil {
			fail(w, 400, err.Error())
			return
		}
		if a.onSelection != nil {
			a.onSelection(selected)
		}
	}
	if r.URL.RawQuery != "" {
		path += "?" + r.URL.RawQuery
	}
	resp, err := a.send(r.Context(), r.Method, path, r.Header, b)
	if err != nil {
		fail(w, 502, "AICode transport failed")
		return
	}
	defer resp.Body.Close()
	copyHeaders(w.Header(), resp.Header)
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(flushingWriter{w}, resp.Body)
}

func explicitModel(args []string) bool {
	for _, arg := range args {
		if arg == "--" {
			break
		}
		if arg == "--model" || arg == "-m" || strings.HasPrefix(arg, "--model=") || strings.HasPrefix(arg, "-m=") {
			return true
		}
	}
	return false
}

func overrideEnv(env []string, overrides map[string]string) []string {
	var result []string
	for _, e := range env {
		k, _, _ := strings.Cut(e, "=")
		if _, ok := overrides[strings.ToUpper(k)]; !ok {
			result = append(result, e)
		}
	}
	for k, v := range overrides {
		result = append(result, k+"="+v)
	}
	return result
}

func run() int {
	exe, err := os.Executable()
	if err != nil {
		fmt.Fprintln(os.Stderr, "agy: cannot locate launcher")
		return 1
	}
	native := filepath.Join(filepath.Dir(exe), "agy-aicode-native.exe")
	b, err := os.ReadFile(native)
	if err != nil {
		fmt.Fprintln(os.Stderr, "agy: missing native companion; reinstall compatibility patch")
		return 1
	}
	sum := sha256.Sum256(b)
	b = nil
	if nativeSHA256 == "" || hex.EncodeToString(sum[:]) != nativeSHA256 {
		fmt.Fprintln(os.Stderr, "agy: native companion hash mismatch; reinstall compatibility patch")
		return 1
	}
	// Hub has a separate authentication and routing contract.
	for _, arg := range os.Args[1:] {
		if hubEnabled != "true" && (arg == "--hub" || strings.HasPrefix(arg, "--hub=")) {
			fmt.Fprintln(os.Stderr, "agy: AICode compatibility currently supports ordinary CLI only")
			return 2
		}
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		fmt.Fprintln(os.Stderr, "agy: cannot start local compatibility adapter")
		return 1
	}
	defer listener.Close()
	var random [24]byte
	if _, err = rand.Read(random[:]); err != nil {
		return 1
	}
	nonce := hex.EncodeToString(random[:])
	a := newAdapter(nonce, explicitModel(os.Args[1:]))
	var notice sync.Once
	a.onSelection = func(name string) { notice.Do(func() { fmt.Fprintln(os.Stderr, "agy: server experience "+name) }) }
	server := &http.Server{Handler: a, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 64 << 10, ErrorLog: log.New(io.Discard, "", 0)}
	go func() { _ = server.Serve(listener) }()
	defer server.Close()
	cmd := exec.Command(native, os.Args[1:]...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	noProxy := os.Getenv("NO_PROXY")
	if noProxy == "" {
		noProxy = os.Getenv("no_proxy")
	}
	cmd.Env = overrideEnv(os.Environ(), map[string]string{"USE_AICODE": "true", "AGY_CLI_DISABLE_AUTO_UPDATE": "true", "AICODE_ENDPOINT_URL": "http://" + listener.Addr().String() + "/" + nonce, "NO_PROXY": noProxy + ",127.0.0.1,localhost"})
	// Both processes share the console. Native agy handles Ctrl+C; keep its parent alive.
	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, os.Interrupt)
	defer signal.Stop(interrupt)
	err = cmd.Start()
	if err != nil {
		fmt.Fprintln(os.Stderr, "agy: cannot start native companion")
		return 1
	}
	release, err := trackChild(cmd)
	if err != nil {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		fmt.Fprintln(os.Stderr, "agy: cannot track native companion lifetime:", err)
		return 1
	}
	defer release()
	err = cmd.Wait()
	if err == nil {
		return 0
	}
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return exit.ExitCode()
	}
	fmt.Fprintln(os.Stderr, "agy: cannot launch native companion")
	return 1
}

func main() { os.Exit(run()) }
