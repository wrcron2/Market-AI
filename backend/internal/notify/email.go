package notify

import (
	"fmt"
	"net/smtp"
	"os"
	"strings"
	"sync"
	"time"

	"go.uber.org/zap"
)

type Notifier struct {
	from     string
	password string
	to       string
	host     string
	port     string
	logger   *zap.Logger

	mu       sync.Mutex
	lastSent map[string]time.Time
}

const cooldown = 15 * time.Minute

func New(logger *zap.Logger) *Notifier {
	return &Notifier{
		from:     os.Getenv("SMTP_FROM"),
		password: os.Getenv("SMTP_PASSWORD"),
		to:       os.Getenv("ALERT_EMAIL_TO"),
		host:     envOr("SMTP_HOST", "smtp.gmail.com"),
		port:     envOr("SMTP_PORT", "587"),
		logger:   logger,
		lastSent: make(map[string]time.Time),
	}
}

func (n *Notifier) Enabled() bool {
	return n.from != "" && n.password != "" && n.to != ""
}

func (n *Notifier) Send(severity, title, body string) error {
	if !n.Enabled() {
		n.logger.Debug("notify.email_skipped", zap.String("reason", "not configured"))
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
	recipients := strings.Split(n.to, ",")
	msg := fmt.Sprintf("From: %s\r\nTo: %s\r\nSubject: %s\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n%s\r\n\r\n— MarketFlow AI Alert System",
		n.from, n.to, subject, body)

	auth := smtp.PlainAuth("", n.from, n.password, n.host)
	addr := n.host + ":" + n.port

	if err := smtp.SendMail(addr, auth, n.from, recipients, []byte(msg)); err != nil {
		n.logger.Error("notify.email_failed", zap.Error(err))
		return err
	}
	n.logger.Info("notify.email_sent", zap.String("severity", severity), zap.String("title", title))
	return nil
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
