package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/marketflow/backend/internal/decisionauthority"
	"github.com/marketflow/backend/internal/operatingmode"
)

func learnerTestEnv(t *testing.T) map[string]string {
	t.Helper()
	return map[string]string{"DECISION_AUTHORITY": "KIMI", "MARKET_AI_OPERATING_MODE": "paper", "PAPER_TRADING": "true", "ALPACA_BASE_URL": "https://paper-api.alpaca.markets", "ALPACA_API_KEY": "synthetic-key", "ALPACA_SECRET_KEY": "synthetic-secret", "KIMI_ALPACA_ACCOUNT_ID": "synthetic-account", "KIMI_OWNER_ID": "1", "KIMI_EXECUTION_HMAC_KEY": "0123456789abcdef0123456789abcdef", "KIMI_EXECUTION_DB": filepath.Join(t.TempDir(), "learner.db"), "KIMI_EXECUTION_ENABLED": "false", "KIMI_KILL_SWITCH": "true", "GO_SERVER_HOST": "127.0.0.1"}
}
func TestDedicatedHandlerWiresAuthenticatedRuntimeWithoutEnablingOrders(t *testing.T) {
	for key, value := range learnerTestEnv(t) {
		t.Setenv(key, value)
	}
	h, closeRuntime := newDedicatedHandler(decisionauthority.Kimi, operatingmode.Paper)
	defer closeRuntime()
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/api/learner/observations?symbol=AAPL", nil))
	if w.Code != 401 {
		t.Fatalf("configured handler not wired: %d", w.Code)
	}
	w = httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/api/operating-mode", nil))
	var status map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &status); err != nil {
		t.Fatal(err)
	}
	if status["learnerConfigured"] != true || status["executionEnabled"] != false {
		t.Fatalf("wrong flags %+v", status)
	}
}

func TestDedicatedProcess(t *testing.T) {
	if os.Getenv("KIMI_TEST_CHILD") == "1" {
		main()
		return
	}
	port := func(t *testing.T) string {
		t.Helper()
		listener, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		defer listener.Close()
		return fmt.Sprint(listener.Addr().(*net.TCPAddr).Port)
	}
	for _, authority := range []string{"KIMI", "DISABLED"} {
		t.Run(authority, func(t *testing.T) {
			env := learnerTestEnv(t)
			env["DECISION_AUTHORITY"] = authority
			env["GO_SERVER_PORT"] = port(t)
			env["GO_GRPC_PORT"] = port(t)
			env["KIMI_TEST_CHILD"] = "1"
			env["DB_DSN"] = filepath.Join(t.TempDir(), "forbidden-legacy.db")
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestDedicatedProcess$")
			for _, entry := range os.Environ() {
				key, _, _ := strings.Cut(entry, "=")
				if _, replace := env[key]; !replace {
					cmd.Env = append(cmd.Env, entry)
				}
			}
			for key, value := range env {
				cmd.Env = append(cmd.Env, key+"="+value)
			}
			var output bytes.Buffer
			cmd.Stdout = &output
			cmd.Stderr = &output
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			done := make(chan error, 1)
			go func() { done <- cmd.Wait() }()
			finished := false
			defer func() {
				if !finished {
					_ = cmd.Process.Kill()
					<-done
				}
			}()
			base := "http://127.0.0.1:" + env["GO_SERVER_PORT"]
			client := &http.Client{Timeout: time.Second}
			deadline := time.Now().Add(8 * time.Second)
			ready := false
			for time.Now().Before(deadline) {
				response, err := client.Get(base + "/healthz")
				if err == nil {
					response.Body.Close()
					ready = response.StatusCode == 200
					if ready {
						break
					}
				}
				time.Sleep(25 * time.Millisecond)
			}
			if !ready {
				t.Fatal("dedicated child never became healthy")
			}
			for _, method := range []string{"GET", "POST", "DELETE"} {
				for _, path := range []string{"/api/orders/approve", "/api/orders/retry", "/api/orders/auto-execute", "/api/alpaca/positions/AAPL/close", "/api/signals", "/api/mode"} {
					req, _ := http.NewRequest(method, base+path, nil)
					response, err := client.Do(req)
					if err != nil {
						t.Fatal(err)
					}
					response.Body.Close()
					if response.StatusCode != 404 {
						t.Fatalf("legacy path reachable: %s %s %d", method, path, response.StatusCode)
					}
				}
			}
			listener, err := net.Listen("tcp", "127.0.0.1:"+env["GO_GRPC_PORT"])
			if err != nil {
				t.Fatal("legacy gRPC port was opened")
			}
			listener.Close()
			if _, err = os.Stat(env["DB_DSN"]); !os.IsNotExist(err) {
				t.Fatal("legacy database initialized")
			}
			if err = cmd.Process.Signal(syscall.SIGTERM); err != nil {
				t.Fatal(err)
			}
			select {
			case err = <-done:
				finished = true
				if err != nil {
					t.Fatalf("shutdown %v: %s", err, output.String())
				}
			case <-time.After(5 * time.Second):
				t.Fatal("graceful shutdown timed out")
			}
		})
	}
}

func TestDedicatedBindFailureExitsNonzero(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestDedicatedProcess$")
	cmd.Env = []string{"KIMI_TEST_CHILD=1", "DECISION_AUTHORITY=DISABLED", "GO_SERVER_HOST=127.0.0.1", "GO_SERVER_PORT=" + fmt.Sprint(listener.Addr().(*net.TCPAddr).Port)}
	err = cmd.Run()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() == 0 || ctx.Err() != nil {
		t.Fatalf("bind failure must exit nonzero promptly, got %v", err)
	}
}
