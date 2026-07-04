// Package notify sends alert emails. Preferred transport is the Resend HTTP
// API (RESEND_API_KEY + ALERT_EMAIL_TO); classic SMTP (SMTP_FROM +
// SMTP_PASSWORD) is kept as a fallback for self-hosted setups.
package notify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/smtp"
	"os"
	"strings"
	"sync"
	"time"

	"go.uber.org/zap"
)

type Notifier struct {
	resendKey  string
	resendFrom string
	from       string
	password   string
	to         string
	host       string
	port       string
	logger     *zap.Logger
	http       *http.Client

	mu       sync.Mutex
	lastSent map[string]time.Time
}

const cooldown = 15 * time.Minute

func New(logger *zap.Logger) *Notifier {
	return &Notifier{
		resendKey:  os.Getenv("RESEND_API_KEY"),
		resendFrom: envOr("RESEND_FROM", "MarketFlow AI <onboarding@resend.dev>"),
		from:       os.Getenv("SMTP_FROM"),
		password:   os.Getenv("SMTP_PASSWORD"),
		to:         os.Getenv("ALERT_EMAIL_TO"),
		host:       envOr("SMTP_HOST", "smtp.gmail.com"),
		port:       envOr("SMTP_PORT", "587"),
		logger:     logger,
		http:       &http.Client{Timeout: 15 * time.Second},
		lastSent:   make(map[string]time.Time),
	}
}

func (n *Notifier) Enabled() bool {
	if n.to == "" {
		return false
	}
	return n.resendKey != "" || (n.from != "" && n.password != "")
}

func (n *Notifier) Send(severity, title, body string) error {
	if !n.Enabled() {
		n.logger.Warn("notify.email_skipped",
			zap.String("reason", "not configured — need ALERT_EMAIL_TO plus RESEND_API_KEY or SMTP_FROM/SMTP_PASSWORD"),
			zap.String("title", title))
		return nil
	}

	n.mu.Lock()
	key := severity + ":" + title
	if last, ok := n.lastSent[key]; ok && time.Since(last) < cooldown {
		n.mu.Unlock()
		return nil
	}
	n.lastSent[key] = time.Now()
	n.mu.Unlock()

	subject := fmt.Sprintf("[MarketFlow %s] %s", severity, title)

	var err error
	if n.resendKey != "" {
		err = n.sendResend(subject, body)
	} else {
		err = n.sendSMTP(subject, body)
	}
	if err != nil {
		n.logger.Error("notify.email_failed", zap.Error(err))
		return err
	}
	n.logger.Info("notify.email_sent", zap.String("severity", severity), zap.String("title", title))
	return nil
}

func (n *Notifier) sendResend(subject, body string) error {
	payload := map[string]any{
		"from":    n.resendFrom,
		"to":      strings.Split(n.to, ","),
		"subject": subject,
		"text":    body + "\n\n— MarketFlow AI Alert System",
	}
	b, _ := json.Marshal(payload)

	req, _ := http.NewRequest("POST", "https://api.resend.com/emails", bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+n.resendKey)

	resp, err := n.http.Do(req)
	if err != nil {
		return fmt.Errorf("resend request failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("resend API error %d: %.300s", resp.StatusCode, string(respBody))
	}
	return nil
}

func (n *Notifier) sendSMTP(subject, body string) error {
	recipients := strings.Split(n.to, ",")
	msg := fmt.Sprintf("From: %s\r\nTo: %s\r\nSubject: %s\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n%s\r\n\r\n— MarketFlow AI Alert System",
		n.from, n.to, subject, body)
	auth := smtp.PlainAuth("", n.from, n.password, n.host)
	addr := n.host + ":" + n.port
	return smtp.SendMail(addr, auth, n.from, recipients, []byte(msg))
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
