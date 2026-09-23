package learnerexecution

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

type NonceStore interface {
	Consume(context.Context, string, string, int64) error
}
type SQLNonceStore struct{ db *sql.DB }

func NewNonceStore(db *sql.DB) (*SQLNonceStore, error) {
	if db == nil {
		return nil, errors.New("nonce_store_unavailable")
	}
	_, e := db.Exec(`CREATE TABLE IF NOT EXISTS learner_request_nonces (
 key_id TEXT NOT NULL, nonce TEXT NOT NULL, expires_at INTEGER NOT NULL,
 PRIMARY KEY(key_id,nonce))`)
	if e != nil {
		return nil, errors.New("nonce_store_unavailable")
	}
	return &SQLNonceStore{db: db}, nil
}
func (s *SQLNonceStore) Consume(ctx context.Context, keyID, nonce string, expiresAt int64) error {
	_, e := s.db.ExecContext(ctx, `INSERT INTO learner_request_nonces(key_id,nonce,expires_at) VALUES(?,?,?)`, keyID, nonce, expiresAt)
	if e != nil {
		return errors.New("nonce_replayed_or_store_unavailable")
	}
	return nil
}

type AuthConfig struct {
	Key       []byte
	OwnerID   string
	AccountID string
}
type Authenticator struct {
	key       []byte
	keyID     string
	ownerID   string
	accountID string
	nonces    NonceStore
	now       func() time.Time
}

func NewAuthenticator(c AuthConfig, nonces NonceStore) (*Authenticator, error) {
	if len(c.Key) < 32 || len(c.Key) > 4096 || !idPattern.MatchString(c.OwnerID) || !idPattern.MatchString(c.AccountID) || nonces == nil {
		return nil, errors.New("invalid_auth_configuration")
	}
	digest := sha256.Sum256(c.Key)
	return &Authenticator{key: append([]byte(nil), c.Key...), keyID: hex.EncodeToString(digest[:]), ownerID: c.OwnerID, accountID: c.AccountID, nonces: nonces, now: time.Now}, nil
}
func (a *Authenticator) Owns(i Intent) bool {
	return i.OwnerID == a.ownerID && i.AccountID == a.accountID
}

// Middleware authenticates every method, including status reads. Nonces are
// committed before dispatch, so a restart never makes a consumed signature valid.
func (a *Authenticator) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		deny := func() { http.Error(w, "request_authentication_failed", http.StatusUnauthorized) }
		if r.Method != "GET" && r.Method != "POST" && r.Method != "DELETE" {
			deny()
			return
		}
		target := r.RequestURI
		if !strings.HasPrefix(target, "/") || strings.HasPrefix(target, "//") || strings.ContainsAny(target, "\r\n#") || len(target) > 2048 {
			deny()
			return
		}
		for _, name := range []string{"X-Kimi-Timestamp", "X-Kimi-Nonce", "X-Kimi-Signature"} {
			if len(r.Header.Values(name)) != 1 {
				deny()
				return
			}
		}
		timestamp, nonce, signature := r.Header.Get("X-Kimi-Timestamp"), r.Header.Get("X-Kimi-Nonce"), r.Header.Get("X-Kimi-Signature")
		ts, e := strconv.ParseInt(timestamp, 10, 64)
		now := a.now().Unix()
		if e != nil || ts <= 0 || strconv.FormatInt(ts, 10) != timestamp || ts > now || now-ts > 60 || !uuidPattern.MatchString(nonce) || !hashPattern.MatchString(signature) {
			deny()
			return
		}
		var body []byte
		if r.Body != nil {
			body, e = io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBodyBytes))
			if e != nil {
				http.Error(w, "request_body_too_large_or_unreadable", http.StatusRequestEntityTooLarge)
				return
			}
		}
		digest := sha256.Sum256(body)
		msg := fmt.Sprintf("%s\n%s\n%s\n%s\n%x", r.Method, target, timestamp, nonce, digest)
		mac := hmac.New(sha256.New, a.key)
		mac.Write([]byte(msg))
		provided, _ := hex.DecodeString(signature)
		if !hmac.Equal(mac.Sum(nil), provided) {
			deny()
			return
		}
		if e = a.nonces.Consume(r.Context(), a.keyID, nonce, ts+60); e != nil {
			deny()
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
		next.ServeHTTP(w, r)
	})
}
