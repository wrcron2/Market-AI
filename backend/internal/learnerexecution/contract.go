// Package learnerexecution contains no strategy or model dependencies.
package learnerexecution

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"reflect"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

const MaxBodyBytes = 65536

type Order struct {
	Side        string `json:"side"`
	Qty         string `json:"qty"`
	Type        string `json:"type"`
	LimitPrice  string `json:"limitPrice"`
	TimeInForce string `json:"timeInForce"`
}
type Knowledge struct {
	SnapshotID string `json:"snapshotId"`
	SHA256     string `json:"sha256"`
}
type Conditions struct {
	Entry        *string `json:"entry"`
	Exit         *string `json:"exit"`
	Invalidation *string `json:"invalidation"`
}
type Producer struct {
	Agent   string `json:"agent"`
	Model   string `json:"model"`
	Version string `json:"version"`
}
type Intent struct {
	Version       string     `json:"version"`
	DecisionID    string     `json:"decisionId"`
	CorrelationID string     `json:"correlationId"`
	OwnerID       string     `json:"ownerId"`
	AccountID     string     `json:"accountId"`
	Environment   string     `json:"environment"`
	PredictionID  int64      `json:"predictionId"`
	Knowledge     Knowledge  `json:"knowledge"`
	Symbol        string     `json:"symbol"`
	Action        string     `json:"action"`
	Order         *Order     `json:"order"`
	DecidedAt     string     `json:"decidedAt"`
	ExpiresAt     string     `json:"expiresAt"`
	ObservationID string     `json:"observationId"`
	MarketDataAt  string     `json:"marketDataAt"`
	Rationale     string     `json:"rationale"`
	EvidenceRefs  []string   `json:"evidenceRefs"`
	Conditions    Conditions `json:"conditions"`
	Producer      Producer   `json:"producer"`
	PolicyVersion string     `json:"policyVersion"`
}

var idPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$`)
var producerPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9:._/+-]{0,127}$`)
var uuidPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
var hashPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var symbolPattern = regexp.MustCompile(`^[A-Z][A-Z0-9.-]{0,14}$`)
var qtyPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,8})(\.[0-9]{1,9})?$`)
var pricePattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,8})(\.[0-9]{1,4})?$`)

const timestampLayout = "2006-01-02T15:04:05.000Z"

func validText(s string, max int) bool { return strings.TrimSpace(s) != "" && len(s) <= max }
func decimal(s string, p *regexp.Regexp) (*big.Rat, error) {
	if !p.MatchString(s) {
		return nil, errors.New("invalid_decimal")
	}
	n, ok := new(big.Rat).SetString(s)
	if !ok || n.Sign() <= 0 {
		return nil, errors.New("invalid_decimal")
	}
	return n, nil
}
func stamp(s string) (time.Time, error) {
	t, e := time.Parse(timestampLayout, s)
	if e != nil || t.Format(timestampLayout) != s {
		return time.Time{}, errors.New("invalid_timestamp")
	}
	return t, nil
}

// readValue rejects duplicate keys before encoding/json can silently overwrite them.
func readValue(d *json.Decoder, depth int) (any, error) {
	if depth > 8 {
		return nil, errors.New("json_too_deep")
	}
	tok, e := d.Token()
	if e != nil {
		return nil, e
	}
	delim, ok := tok.(json.Delim)
	if !ok {
		return tok, nil
	}
	switch delim {
	case '{':
		obj := map[string]any{}
		for d.More() {
			key, e := d.Token()
			if e != nil {
				return nil, e
			}
			name, ok := key.(string)
			if !ok {
				return nil, errors.New("invalid_key")
			}
			if _, exists := obj[name]; exists {
				return nil, errors.New("duplicate_key")
			}
			v, e := readValue(d, depth+1)
			if e != nil {
				return nil, e
			}
			obj[name] = v
		}
		end, e := d.Token()
		if e != nil || end != json.Delim('}') {
			return nil, errors.New("invalid_object")
		}
		return obj, nil
	case '[':
		items := []any{}
		for d.More() {
			v, e := readValue(d, depth+1)
			if e != nil {
				return nil, e
			}
			items = append(items, v)
		}
		end, e := d.Token()
		if e != nil || end != json.Delim(']') {
			return nil, errors.New("invalid_array")
		}
		return items, nil
	default:
		return nil, errors.New("unexpected_delimiter")
	}
}

// exactShape rejects missing fields, null non-nullables and case-insensitive aliases.
func exactShape(value any, typ reflect.Type) error {
	if typ.Kind() == reflect.Pointer {
		if value == nil {
			return nil
		}
		return exactShape(value, typ.Elem())
	}
	if value == nil {
		return errors.New("null_required_field")
	}
	switch typ.Kind() {
	case reflect.Struct:
		obj, ok := value.(map[string]any)
		if !ok || len(obj) != typ.NumField() {
			return errors.New("invalid_object_fields")
		}
		for n := 0; n < typ.NumField(); n++ {
			f := typ.Field(n)
			v, ok := obj[f.Tag.Get("json")]
			if !ok {
				return errors.New("missing_field")
			}
			if e := exactShape(v, f.Type); e != nil {
				return e
			}
		}
	case reflect.Slice:
		arr, ok := value.([]any)
		if !ok {
			return errors.New("invalid_array")
		}
		for _, v := range arr {
			if e := exactShape(v, typ.Elem()); e != nil {
				return e
			}
		}
	case reflect.String:
		if _, ok := value.(string); !ok {
			return errors.New("invalid_string")
		}
	case reflect.Int64:
		if _, ok := value.(json.Number); !ok {
			return errors.New("invalid_number")
		}
	}
	return nil
}
func DecodeIntent(raw []byte) (Intent, error) {
	var i Intent
	if len(raw) > MaxBodyBytes || !utf8.Valid(raw) {
		return i, errors.New("invalid_body")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	value, e := readValue(d, 0)
	if e != nil {
		return i, e
	}
	if _, e = d.Token(); e != io.EOF {
		return i, errors.New("trailing_json")
	}
	if e = exactShape(value, reflect.TypeOf(i)); e != nil {
		return i, e
	}
	if e = json.Unmarshal(raw, &i); e != nil {
		return i, errors.New("invalid_intent")
	}
	return i, i.Validate()
}
func (i Intent) Validate() error {
	invalid := errors.New("invalid_intent")
	if i.Version != "trade-intent-v1" || i.Environment != "paper" || i.Producer.Agent != "kimi-learner" || !uuidPattern.MatchString(i.DecisionID) || !uuidPattern.MatchString(i.CorrelationID) {
		return invalid
	}
	for _, s := range []string{i.OwnerID, i.AccountID, i.Knowledge.SnapshotID, i.ObservationID, i.PolicyVersion} {
		if !idPattern.MatchString(s) {
			return invalid
		}
	}
	if !producerPattern.MatchString(i.Producer.Model) || !producerPattern.MatchString(i.Producer.Version) {
		return invalid
	}
	if !hashPattern.MatchString(i.Knowledge.SHA256) || !symbolPattern.MatchString(i.Symbol) || i.PredictionID <= 0 || i.PredictionID > 9007199254740991 || !validText(i.Rationale, 4000) {
		return invalid
	}
	if len(i.EvidenceRefs) < 1 || len(i.EvidenceRefs) > 20 {
		return invalid
	}
	for _, s := range i.EvidenceRefs {
		if !validText(s, 512) {
			return invalid
		}
	}
	for _, s := range []*string{i.Conditions.Entry, i.Conditions.Exit, i.Conditions.Invalidation} {
		if s != nil && !validText(*s, 2000) {
			return invalid
		}
	}
	if i.Action == "HOLD" {
		if i.Order != nil {
			return invalid
		}
	} else {
		if i.Action != "BUY" && i.Action != "SELL" && i.Action != "EXIT" {
			return invalid
		}
		if i.Order == nil {
			return invalid
		}
		o := i.Order
		side := "sell"
		if i.Action == "BUY" {
			side = "buy"
		}
		if o.Side != side || o.Type != "limit" || o.TimeInForce != "day" {
			return invalid
		}
		if _, e := decimal(o.Qty, qtyPattern); e != nil {
			return invalid
		}
		if _, e := decimal(o.LimitPrice, pricePattern); e != nil {
			return invalid
		}
	}
	decided, e := stamp(i.DecidedAt)
	if e != nil {
		return e
	}
	expires, e := stamp(i.ExpiresAt)
	if e != nil {
		return e
	}
	market, e := stamp(i.MarketDataAt)
	if e != nil {
		return e
	}
	if !expires.After(decided) || expires.Sub(decided) > 5*time.Minute || market.After(decided) {
		return invalid
	}
	return nil
}
func (i Intent) ValidateFresh(now time.Time) error {
	if e := i.Validate(); e != nil {
		return e
	}
	decided, _ := stamp(i.DecidedAt)
	expires, _ := stamp(i.ExpiresAt)
	if now.Before(decided) || !now.Before(expires) {
		return fmt.Errorf("intent_expired_or_future")
	}
	return nil
}
