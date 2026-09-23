package learnerexecution

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/marketflow/backend/internal/decisionauthority"
	"github.com/marketflow/backend/internal/operatingmode"
)

// Test-only child entrypoint used by Kimi's verify-learner-execution.ts.
// Never compiled into the product; no fake-host switch exists in runtime code.
func TestLearnerHTTPProcess(t *testing.T) {
	if os.Getenv("LEARNER_HTTP_TEST") != "1" {
		return
	}
	fake, err := url.Parse(os.Getenv("LEARNER_HTTP_BROKER"))
	if err != nil || fake.Scheme != "http" || fake.Hostname() != "127.0.0.1" || fake.Port() == "" || fake.Path != "" {
		t.Fatal("test broker must be explicit loopback")
	}
	path := os.Getenv("LEARNER_HTTP_DB")
	if !filepath.IsAbs(path) || filepath.Base(path) != "execution.sqlite" {
		t.Fatal("test journal must be an explicit absolute execution.sqlite path")
	}
	owner := os.Getenv("LEARNER_HTTP_OWNER")
	const account = "synthetic-http-paper"
	// Dial guard guarantees the adapter cannot reach an external network even
	// if a future code change bypasses its official-host whitelist.
	transport := &http.Transport{Proxy: nil, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != fake.Host {
			return nil, errors.New("external_network_forbidden_in_test")
		}
		return (&net.Dialer{Timeout: time.Second}).DialContext(ctx, network, address)
	}}
	defer transport.CloseIdleConnections()
	broker, err := newPaperBroker(PaperBrokerConfig{BaseURL: operatingmode.PaperBrokerBaseURL, APIKey: "synthetic-key", SecretKey: "synthetic-secret", AccountID: account, Authority: "KIMI", OperatingMode: "paper", Paper: true}, paperTransportFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.Scheme != "https" || (r.URL.Host != "paper-api.alpaca.markets" && r.URL.Host != "data.alpaca.markets") {
			return nil, errors.New("unexpected_broker_host")
		}
		copy := r.Clone(r.Context())
		u := *r.URL
		u.Scheme, u.Host = fake.Scheme, fake.Host
		copy.URL, copy.Host = &u, fake.Host
		return transport.RoundTrip(copy)
	}))
	if err != nil {
		t.Fatal(err)
	}
	location := url.URL{Scheme: "file", Path: path, RawQuery: "_busy_timeout=5000&_journal_mode=WAL&_synchronous=FULL&_foreign_keys=on"}
	db, err := sql.Open("sqlite3", location.String())
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(8)
	store, err := NewStore(db, owner, account)
	if err != nil {
		t.Fatal(err)
	}
	nonces, err := NewNonceStore(db)
	if err != nil {
		t.Fatal(err)
	}
	auth, err := NewAuthenticator(AuthConfig{Key: []byte("synthetic-local-test-key-not-a-secret-0123456789"), OwnerID: owner, AccountID: account}, nonces)
	if err != nil {
		t.Fatal(err)
	}
	service, err := NewExecutionService(store, broker, SafetyPolicy{Enabled: true, AccountID: account, Version: "paper-v1", MaxOrder: "10", MaxPosition: "20", MaxPortfolio: "30", MaxDaily: "100", MaxAge: 30 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	handler, err := NewHandler(auth, service)
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	fmt.Printf("LEARNER_HTTP_READY http://%s\n", listener.Addr())
	server := &http.Server{Handler: decisionauthority.NewMux(decisionauthority.Kimi, "paper", handler, true), ReadHeaderTimeout: 5 * time.Second}
	if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
		t.Fatal(err)
	}
}
