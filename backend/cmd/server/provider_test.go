package main

import (
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

func TestLocalProviderHandlerPreservesDisabledAWS(t *testing.T) {
	changes := 0
	handler := newLLMProviderHandler(func(provider string) {
		if provider != "local" {
			t.Fatal("paid provider enabled")
		}
		changes++
	})
	for _, tc := range []struct {
		method, body string
		status       int
	}{
		{"GET", "", 200}, {"POST", `{"provider":"aws"}`, 403}, {"POST", `{"provider":"gcp"}`, 400}, {"POST", `{"provider":"local"}`, 200}, {"POST", `broken`, 400}, {"DELETE", "", 405},
	} {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(tc.method, "/api/llm-provider", strings.NewReader(tc.body)))
		if w.Code != tc.status {
			t.Fatalf("%s %s: %d", tc.method, tc.body, w.Code)
		}
	}
	if changes != 1 {
		t.Fatalf("unexpected provider changes: %d", changes)
	}
}

// Launches only the real provider-setting handler. No model clients, strategies,
// DB, gRPC, broker, ordinary main(), or production environment are initialized.
func TestProviderHTTPFixture(t *testing.T) {
	if os.Getenv("PROVIDER_HTTP_TEST") != "1" {
		return
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { _, _ = io.WriteString(w, "ok") })
	mux.Handle("/api/llm-provider", newLLMProviderHandler(func(string) {}))
	l, err := net.Listen("tcp", "127.0.0.1:18080")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	s := &http.Server{Handler: mux, ReadHeaderTimeout: time.Second}
	if err := s.Serve(l); err != nil {
		t.Fatal(err)
	}
}
