//go:build !windows

package main

import "os/exec"

func trackChild(cmd *exec.Cmd) (func(), error) { return func() {}, nil }
