package learnerexecution

import (
	"errors"
	"math/big"
	"strings"
)

// externalPositions parses an operator-recorded baseline, never broker data or
// model output. These symbols are reserved: the learner cannot buy or sell them.
func (p SafetyPolicy) externalPositions() (map[string]string, error) {
	positions := map[string]string{}
	if p.ExternalPositions == "" {
		return positions, nil
	}
	entries := strings.Split(p.ExternalPositions, ",")
	if len(entries) > 50 {
		return nil, errors.New("external_positions_invalid")
	}
	for _, entry := range entries {
		symbol, qty, ok := strings.Cut(entry, "=")
		if !ok || !symbolPattern.MatchString(symbol) {
			return nil, errors.New("external_positions_invalid")
		}
		if _, err := decimal(strings.TrimPrefix(qty, "-"), qtyPattern); err != nil {
			return nil, errors.New("external_positions_invalid")
		}
		if _, duplicate := positions[symbol]; duplicate {
			return nil, errors.New("external_positions_invalid")
		}
		positions[symbol] = qty
	}
	return positions, nil
}

// Signed values are allowed only for observed external positions, never orders
// or learner-owned quantities. Preserve the broker's direction exactly.
func signedPositionDecimal(text string) (*big.Rat, error) {
	n, err := observedDecimal(strings.TrimPrefix(text, "-"))
	if err != nil {
		return nil, err
	}
	if strings.HasPrefix(text, "-") {
		if n.Sign() == 0 {
			return nil, errors.New("invalid_broker_decimal")
		}
		n.Neg(n)
	}
	return n, nil
}
