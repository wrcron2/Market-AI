package learnerexecution

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestIntentIngressRejectsWrongScopeBeforeExecution(t *testing.T) {
	for _, name := range []string{"valid", "wrong owner", "wrong account", "duplicate key", "unknown field", "wrong path", "query", "wrong method"} {
		t.Run(name, func(t *testing.T) {
			db := testNonceDB(t, filepath.Join(t.TempDir(), "nonce.db"))
			store, e := NewNonceStore(db)
			if e != nil {
				t.Fatal(e)
			}
			auth, e := NewAuthenticator(AuthConfig{Key: []byte(testKey), OwnerID: "1", AccountID: "paper-account-1"}, store)
			if e != nil {
				t.Fatal(e)
			}
			auth.now = func() time.Time { return fixtureTime() }
			raw := string(fixtureBytes(t))
			method, path := "POST", "/api/learner/intents"
			switch name {
			case "wrong owner":
				raw = strings.Replace(raw, `"ownerId": "1"`, `"ownerId":"2"`, 1)
			case "wrong account":
				raw = strings.Replace(raw, "paper-account-1", "other-account", 1)
			case "duplicate key":
				raw = strings.Replace(raw, `"action": "BUY"`, `"action":"SELL","action":"BUY"`, 1)
			case "unknown field":
				raw = strings.Replace(raw, `"action": "BUY"`, `"action":"BUY","override":true`, 1)
			case "wrong path":
				path = "/api/orders/approve"
			case "query":
				path += "?override=1"
			case "wrong method":
				method = "GET"
			}
			calls := 0
			handler := NewIntentHandler(auth, func(w http.ResponseWriter, r *http.Request, i Intent, b []byte) {
				calls++
				if i.Action != "BUY" || !bytes.Equal(b, fixtureBytes(t)) {
					t.Fatal("intent altered by ingress")
				}
				w.WriteHeader(202)
			})
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, signedRequest(method, path, raw, testKey, testNonce, fixtureTime().Unix()))
			if name == "valid" {
				if calls != 1 || w.Code != 202 {
					t.Fatalf("valid rejected: %d", w.Code)
				}
			} else if calls != 0 || w.Code < 400 {
				t.Fatal("invalid intent reached execution")
			}
		})
	}
}

func TestAuthenticatedReadAlsoRequiresSignature(t *testing.T) {
	for _, signed := range []bool{false, true} {
		db := testNonceDB(t, filepath.Join(t.TempDir(), "nonce.db"))
		store, e := NewNonceStore(db)
		if e != nil {
			t.Fatal(e)
		}
		auth, e := NewAuthenticator(AuthConfig{Key: []byte(testKey), OwnerID: "1", AccountID: "paper-account-1"}, store)
		if e != nil {
			t.Fatal(e)
		}
		auth.now = func() time.Time { return fixtureTime() }
		r := signedRequest("GET", "/api/learner/intents/12345678-1234-4234-8234-123456789abc", "", testKey, testNonce, fixtureTime().Unix())
		if !signed {
			r.Header.Del("X-Kimi-Signature")
		}
		calls := 0
		w := httptest.NewRecorder()
		auth.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(200) })).ServeHTTP(w, r)
		if signed && (calls != 1 || w.Code != 200) {
			t.Fatal("signed read rejected")
		}
		if !signed && (calls != 0 || w.Code != 401) {
			t.Fatal("unsigned read dispatched")
		}
	}
}
