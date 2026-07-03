// Package statusreports serves the Chief PM markdown audit reports (reports/*.md)
// and their structured JSON sidecars (reports/*.json) to the dashboard.
package statusreports

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

var reportsDir = getEnv("STATUS_REPORTS_DIR", "../reports")

var validDate = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

// Findings is the RED/YELLOW/GREEN count from a report's Gap Analysis section.
type Findings struct {
	Red    int `json:"red"`
	Yellow int `json:"yellow"`
	Green  int `json:"green"`
}

// Summary is the machine-readable sidecar for one report, written by hand
// alongside the markdown (never scraped from the prose).
type Summary struct {
	Date                     string   `json:"date"`
	Headline                 string   `json:"headline"`
	Equity                   float64  `json:"equity"`
	AllTimeReturnPct         float64  `json:"all_time_return_pct"`
	SignalsPending           int      `json:"signals_pending"`
	OldestPendingDays        *int     `json:"oldest_pending_days"`
	LargestPositionPctEquity float64  `json:"largest_position_pct_equity"`
	LargestPositionSymbol    string   `json:"largest_position_symbol"`
	Findings                 Findings `json:"findings"`
	RedTotalPrior            *int     `json:"red_total_prior"`
	RedResolvedFromPrior     *int     `json:"red_resolved_from_prior"`
}

// List returns every report's summary, newest first, capped at 30.
// GET /api/reports/status
func List(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	entries, err := os.ReadDir(reportsDir)
	if err != nil {
		writeJSON(w, map[string]any{"reports": []Summary{}})
		return
	}

	summaries := make([]Summary, 0, len(entries))
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		data, err := os.ReadFile(filepath.Join(reportsDir, e.Name()))
		if err != nil {
			continue
		}
		var s Summary
		if err := json.Unmarshal(data, &s); err != nil {
			continue
		}
		summaries = append(summaries, s)
	}

	sort.Slice(summaries, func(i, j int) bool { return summaries[i].Date > summaries[j].Date })
	if len(summaries) > 30 {
		summaries = summaries[:30]
	}

	writeJSON(w, map[string]any{"reports": summaries})
}

// Markdown serves the raw report file, byte-for-byte, for download and the
// insight drilldown's rendered view.
// GET /api/reports/status/{date}/markdown
func Markdown(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	date := r.PathValue("date")
	if !validDate.MatchString(date) {
		http.Error(w, "invalid date", http.StatusBadRequest)
		return
	}

	data, err := os.ReadFile(filepath.Join(reportsDir, "status-report-"+date+".md"))
	if err != nil {
		http.Error(w, "report not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
	w.Write(data)
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
