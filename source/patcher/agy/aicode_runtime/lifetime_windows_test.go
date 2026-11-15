package main

import (
	"os"
	"os/exec"
	"testing"
	"time"
	"unsafe"
)

func TestChildLifetimeHelper(t *testing.T) {
	if os.Getenv("AGY_LIFETIME_HELPER") == "true" {
		time.Sleep(time.Hour)
	}
}

func TestChildJobTerminatesCompanionWhenClosed(t *testing.T) {
	if unsafe.Sizeof(extendedJobLimits{}) != 144 {
		t.Fatal("unexpected Windows ABI layout")
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestChildLifetimeHelper$")
	cmd.Env = append(os.Environ(), "AGY_LIFETIME_HELPER=true")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer cmd.Process.Kill()
	release, err := trackChild(cmd)
	if err != nil {
		t.Fatal(err)
	}
	release()
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-done:
		// Windows may report exit code zero when closing a kill-on-close job.
		// The helper's hour-long sleep must still end within this deadline.
	case <-time.After(5 * time.Second):
		t.Fatal("native helper outlived launcher job")
	}
}
