package learnerexecution

import (
	"database/sql"
	"errors"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/marketflow/backend/internal/operatingmode"
	_ "github.com/mattn/go-sqlite3"
)

type RuntimeConfig struct {
	DatabasePath string
	OwnerID      string
	HMACKey      []byte
	Broker       PaperBrokerConfig
	Policy       SafetyPolicy
}
type Runtime struct {
	Handler           http.Handler
	SubmissionEnabled bool
	db                *sql.DB
}

func (r *Runtime) Close() error { return r.db.Close() }

func (p SafetyPolicy) configured() bool {
	if _, err := p.externalPositions(); err != nil {
		return false
	}
	if !idPattern.MatchString(p.AccountID) || !idPattern.MatchString(p.Version) || p.MaxAge <= 0 || p.MaxAge > time.Minute {
		return false
	}
	for _, value := range []string{p.MaxOrder, p.MaxPosition, p.MaxPortfolio, p.MaxDaily} {
		if _, err := decimal(value, pricePattern); err != nil {
			return false
		}
	}
	return true
}

func RuntimeConfigFromEnv() RuntimeConfig {
	account := os.Getenv("KIMI_ALPACA_ACCOUNT_ID")
	age, _ := strconv.Atoi(os.Getenv("KIMI_OBSERVATION_MAX_AGE_SECONDS"))
	if age < 1 || age > 60 {
		age = 0
	}
	base := os.Getenv("ALPACA_BASE_URL")
	if base == "" {
		base = operatingmode.PaperBrokerBaseURL
	}
	return RuntimeConfig{DatabasePath: os.Getenv("KIMI_EXECUTION_DB"), OwnerID: os.Getenv("KIMI_OWNER_ID"), HMACKey: []byte(os.Getenv("KIMI_EXECUTION_HMAC_KEY")),
		Broker: PaperBrokerConfig{BaseURL: base, APIKey: os.Getenv("ALPACA_API_KEY"), SecretKey: os.Getenv("ALPACA_SECRET_KEY"), AccountID: account, Authority: os.Getenv("DECISION_AUTHORITY"), OperatingMode: os.Getenv("MARKET_AI_OPERATING_MODE"), Paper: os.Getenv("PAPER_TRADING") == "true"},
		Policy: SafetyPolicy{ExternalPositions: os.Getenv("KIMI_EXTERNAL_POSITIONS"), Enabled: os.Getenv("KIMI_EXECUTION_ENABLED") == "true", KillSwitch: os.Getenv("KIMI_KILL_SWITCH") != "false", AccountID: account, Version: os.Getenv("KIMI_EXECUTION_POLICY_VERSION"), MaxOrder: os.Getenv("KIMI_MAX_ORDER_NOTIONAL"), MaxPosition: os.Getenv("KIMI_MAX_POSITION_NOTIONAL"), MaxPortfolio: os.Getenv("KIMI_MAX_PORTFOLIO_NOTIONAL"), MaxDaily: os.Getenv("KIMI_MAX_DAILY_NOTIONAL"), MaxAge: time.Duration(age) * time.Second},
	}
}

// OpenRuntime owns a dedicated SQLite file, never the legacy DB/migrations.
// Construction makes no broker calls. Submission requires an explicit opt-in,
// kill-switch release and complete limits; authenticated recovery remains usable.
func OpenRuntime(c RuntimeConfig) (*Runtime, error) {
	if !filepath.IsAbs(c.DatabasePath) || !idPattern.MatchString(c.OwnerID) || len(c.HMACKey) < 32 || len(c.HMACKey) > 4096 || c.Policy.AccountID != c.Broker.AccountID {
		return nil, errors.New("learner_configuration_invalid")
	}
	broker, err := NewPaperBroker(c.Broker)
	if err != nil {
		return nil, err
	}
	location := url.URL{Scheme: "file", Path: filepath.Clean(c.DatabasePath), RawQuery: "_busy_timeout=5000&_journal_mode=WAL&_synchronous=FULL&_foreign_keys=on"}
	db, err := sql.Open("sqlite3", location.String())
	if err != nil {
		return nil, errors.New("learner_storage_unavailable")
	}
	db.SetMaxOpenConns(8)
	success := false
	defer func() {
		if !success {
			db.Close()
		}
	}()
	store, err := NewStore(db, c.OwnerID, c.Broker.AccountID)
	if err != nil {
		return nil, errors.New("learner_storage_unavailable")
	}
	nonces, err := NewNonceStore(db)
	if err != nil {
		return nil, errors.New("learner_storage_unavailable")
	}
	auth, err := NewAuthenticator(AuthConfig{Key: c.HMACKey, OwnerID: c.OwnerID, AccountID: c.Broker.AccountID}, nonces)
	if err != nil {
		return nil, err
	}
	service, err := NewExecutionService(store, broker, c.Policy)
	if err != nil {
		return nil, err
	}
	handler, err := NewHandler(auth, service)
	if err != nil {
		return nil, err
	}
	success = true
	return &Runtime{Handler: handler, SubmissionEnabled: c.Policy.Enabled && !c.Policy.KillSwitch && c.Policy.configured(), db: db}, nil
}
