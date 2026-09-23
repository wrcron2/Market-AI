package learnerexecution

import (
	"errors"
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
		if _, err := decimal(qty, qtyPattern); err != nil {
			return nil, errors.New("external_positions_invalid")
		}
		if _, duplicate := positions[symbol]; duplicate {
			return nil, errors.New("external_positions_invalid")
		}
		positions[symbol] = qty
	}
	return positions, nil
}
