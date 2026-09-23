package learnerexecution

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"fmt"
	_ "github.com/mattn/go-sqlite3"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

const testKey = "0123456789abcdef0123456789abcdef"
const testNonce = "12345678-1234-4234-8234-123456789abc"

func signedRequest(method, target, body, key, nonce string, ts int64) *http.Request {
	r := httptest.NewRequest(method, target, strings.NewReader(body))
	digest := sha256.Sum256([]byte(body))
	msg := fmt.Sprintf("%s\n%s\n%d\n%s\n%x", method, target, ts, nonce, digest)
	mac := hmac.New(sha256.New, []byte(key))
	mac.Write([]byte(msg))
	r.Header.Set("X-Kimi-Timestamp", strconv.FormatInt(ts, 10))
	r.Header.Set("X-Kimi-Nonce", nonce)
	r.Header.Set("X-Kimi-Signature", hex.EncodeToString(mac.Sum(nil)))
	return r
}
func testNonceDB(t *testing.T, path string) *sql.DB {
	t.Helper()
	db, e := sql.Open("sqlite3", path+"?_busy_timeout=5000")
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { db.Close() })
	return db
}
func TestIndependentSignatureAndDurableReplay(t *testing.T) {
	path := filepath.Join(t.TempDir(), "auth.db")
	db := testNonceDB(t, path)
	store, e := NewNonceStore(db)
	if e != nil {
		t.Fatal(e)
	}
	auth, e := NewAuthenticator(AuthConfig{Key: []byte(testKey), OwnerID: "1", AccountID: "paper-account-1"}, store)
	if e != nil {
		t.Fatal(e)
	}
	auth.now = func() time.Time { return time.Unix(1790172001, 0) }
	calls := 0
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(202) })
	request := func() *http.Request {
		return signedRequest("POST", "/api/learner/intents", "{}", testKey, testNonce, 1790172001)
	}
	r := request()
	if r.Header.Get("X-Kimi-Signature") != "7c054711a6ebac25de3377371cfba08319a2bc6039f6ab3df83db85ea1ac87ff" {
		t.Fatal("protocol fixture mismatch")
	}
	w := httptest.NewRecorder()
	auth.Middleware(next).ServeHTTP(w, r)
	if w.Code != 202 || calls != 1 {
		t.Fatalf("valid request rejected %d", w.Code)
	}
	db.Close()
	reopened := testNonceDB(t, path)
	store, e = NewNonceStore(reopened)
	if e != nil {
		t.Fatal(e)
	}
	auth, e = NewAuthenticator(AuthConfig{Key: []byte(testKey), OwnerID: "1", AccountID: "paper-account-1"}, store)
	if e != nil {
		t.Fatal(e)
	}
	auth.now = func() time.Time { return time.Unix(1790172001, 0) }
	w = httptest.NewRecorder()
	auth.Middleware(next).ServeHTTP(w, request())
	if w.Code != 401 || calls != 1 {
		t.Fatal("replay survived restart")
	}
}
func TestAuthenticationRejectsTamperingBeforeHandler(t *testing.T) {
	for _, name := range []string{"missing", "wrong key", "body", "method", "query", "stale", "future", "bad nonce", "duplicate header", "oversize", "store failure"} {
		t.Run(name, func(t *testing.T) {
			db := testNonceDB(t, filepath.Join(t.TempDir(), "auth.db"))
			store, e := NewNonceStore(db)
			if e != nil {
				t.Fatal(e)
			}
			auth, e := NewAuthenticator(AuthConfig{Key: []byte(testKey), OwnerID: "1", AccountID: "paper-account-1"}, store)
			if e != nil {
				t.Fatal(e)
			}
			auth.now = func() time.Time { return time.Unix(1790172001, 0) }
			r := signedRequest("POST", "/api/learner/intents", "{}", testKey, testNonce, 1790172001)
			switch name {
			case "missing":
				r.Header.Del("X-Kimi-Signature")
			case "wrong key":
				r = signedRequest("POST", "/api/learner/intents", "{}", strings.Repeat("x", 32), testNonce, 1790172001)
			case "body":
				r.Body = http.NoBody
			case "method":
				r.Method = "GET"
			case "query":
				r.RequestURI += "?different=1"
			case "stale":
				r = signedRequest("POST", "/api/learner/intents", "{}", testKey, testNonce, 1790171940)
			case "future":
				r = signedRequest("POST", "/api/learner/intents", "{}", testKey, testNonce, 1790172002)
			case "bad nonce":
				r.Header.Set("X-Kimi-Nonce", "bad")
			case "duplicate header":
				r.Header.Add("X-Kimi-Timestamp", "1790172001")
			case "oversize":
				r = signedRequest("POST", "/api/learner/intents", strings.Repeat("x", MaxBodyBytes+1), testKey, testNonce, 1790172001)
			case "store failure":
				db.Close()
			}
			called := false
			w := httptest.NewRecorder()
			auth.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true })).ServeHTTP(w, r)
			if called || w.Code < 400 {
				t.Fatalf("unsafe request accepted %d", w.Code)
			}
		})
	}
}
func TestNonceConcurrentUnique(t *testing.T) {
	db := testNonceDB(t, filepath.Join(t.TempDir(), "auth.db"))
	store, e := NewNonceStore(db)
	if e != nil {
		t.Fatal(e)
	}
	results := make(chan error, 20)
	for n := 0; n < 20; n++ {
		go func() { results <- store.Consume(context.Background(), "key", testNonce, 1790172061) }()
	}
	accepted := 0
	for n := 0; n < 20; n++ {
		if <-results == nil {
			accepted++
		}
	}
	if accepted != 1 {
		t.Fatalf("accepted nonce %d times", accepted)
	}
}
func TestMissingAuthConfigurationFailsClosed(t *testing.T) {
	for _, c := range []AuthConfig{{}, {Key: []byte(testKey)}, {Key: []byte("short"), OwnerID: "1", AccountID: "a"}} {
		if _, e := NewAuthenticator(c, nil); e == nil {
			t.Fatal("invalid auth configured")
		}
	}
}
