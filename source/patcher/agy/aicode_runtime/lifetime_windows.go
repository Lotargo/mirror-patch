package main

import (
	"fmt"
	"os/exec"
	"syscall"
	"unsafe"
)

var jobKernel = syscall.NewLazyDLL("kernel32.dll")
var createJob = jobKernel.NewProc("CreateJobObjectW")
var setJobInfo = jobKernel.NewProc("SetInformationJobObject")
var assignJob = jobKernel.NewProc("AssignProcessToJobObject")
var openJobProcess = jobKernel.NewProc("OpenProcess")
var closeJobHandle = jobKernel.NewProc("CloseHandle")

type basicJobLimits struct {
	ProcessTime, JobTime         int64
	Flags                        uint32
	MinWorkingSet, MaxWorkingSet uintptr
	ActiveProcesses              uint32
	Affinity                     uintptr
	Priority, Scheduling         uint32
}
type extendedJobLimits struct {
	Basic                                                      basicJobLimits
	IO                                                         [6]uint64
	ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory uintptr
}

// Only the launcher owns the job handle. Windows closes it even when Electron
// terminates the launcher, which also terminates the native companion.
func trackChild(cmd *exec.Cmd) (func(), error) {
	job, _, err := createJob.Call(0, 0)
	if job == 0 {
		return nil, fmt.Errorf("create child lifetime job: %w", err)
	}
	release := func() { _, _, _ = closeJobHandle.Call(job) }
	limits := extendedJobLimits{}
	limits.Basic.Flags = 0x2000 // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	ok, _, err := setJobInfo.Call(job, 9, uintptr(unsafe.Pointer(&limits)), unsafe.Sizeof(limits))
	if ok == 0 {
		release()
		return nil, fmt.Errorf("configure child lifetime job: %w", err)
	}
	process, _, err := openJobProcess.Call(0x0101, 0, uintptr(cmd.Process.Pid)) // SET_QUOTA | TERMINATE
	if process == 0 {
		release()
		if err == syscall.Errno(87) {
			return func() {}, nil
		} // --version/--stamp already exited
		return nil, fmt.Errorf("open native child: %w", err)
	}
	defer closeJobHandle.Call(process)
	ok, _, err = assignJob.Call(job, process)
	if ok == 0 {
		release()
		return nil, fmt.Errorf("assign native child lifetime: %w", err)
	}
	return release, nil
}
