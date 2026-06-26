// Package pipeline exposes the HTTP API for the Repo Scout & Research pipeline.
package pipeline

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"

	"go.uber.org/zap"
)

// Handler holds shared state for all pipeline endpoints.
type Handler struct {
	mu              sync.RWMutex
	scoutRunning    bool
	researchRunning bool
	lastScoutRun    *time.Time
	lastResearchRun *time.Time

	projectRoot string
	supabaseURL string
	supabaseKey string
	logger      *zap.Logger
}

// RepoRow mirrors the github_repo_scout Supabase table.
type RepoRow struct {
	ID               int64    `json:"id"`
	FullName         string   `json:"full_name"`
	URL              string   `json:"url"`
	Description      *string  `json:"description"`
	Stars            int      `json:"stars"`
	Language         *string  `json:"language"`
	Topics           []string `json:"topics"`
	LastCommitAt     *string  `json:"last_commit_at"`
	FirstSeenAt      string   `json:"first_seen_at"`
	LastCheckedAt    string   `json:"last_checked_at"`
	Status           string   `json:"status"`
	RejectedReason   *string  `json:"rejected_reason"`
	ResearchNotionURL *string `json:"research_notion_url"`
	ResearchedAt     *string  `json:"researched_at"`
}

// New creates a Handler. projectRoot should be the working directory of the server.
func New(projectRoot, supabaseURL, supabaseKey string, logger *zap.Logger) *Handler {
	return &Handler{
		projectRoot: projectRoot,
		supabaseURL: supabaseURL,
		supabaseKey: supabaseKey,
		logger:      logger,
	}
}

// Repos handles GET /api/pipeline/repos
// Accepts an optional ?status= query param (e.g. "good", "new", "rejected", "researched").
func (h *Handler) Repos(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	if h.supabaseURL == "" || h.supabaseKey == "" {
		writeJSON(w, map[string]any{"repos": []any{}, "error": "Supabase not configured — set SUPABASE_URL and SUPABASE_SERVICE_KEY"})
		return
	}

	statusFilter := r.URL.Query().Get("status")
	apiURL := h.supabaseURL + "/rest/v1/github_repo_scout?select=*&order=first_seen_at.desc"
	if statusFilter != "" {
		apiURL += "&status=eq." + statusFilter
	}

	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, apiURL, nil)
	if err != nil {
		http.Error(w, "failed to build supabase request", http.StatusInternalServerError)
		return
	}
	req.Header.Set("apikey", h.supabaseKey)
	req.Header.Set("Authorization", "Bearer "+h.supabaseKey)
	req.Header.Set("Accept", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		writeJSON(w, map[string]any{"repos": []any{}, "error": "Supabase unreachable: " + err.Error()})
		return
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		writeJSON(w, map[string]any{"repos": []any{}, "error": fmt.Sprintf("Supabase error %d: %s", resp.StatusCode, string(body))})
		return
	}

	var rows []RepoRow
	if err := json.Unmarshal(body, &rows); err != nil {
		writeJSON(w, map[string]any{"repos": []any{}, "error": "parse error: " + err.Error()})
		return
	}
	if rows == nil {
		rows = []RepoRow{}
	}
	writeJSON(w, map[string]any{"repos": rows})
}

// Status handles GET /api/pipeline/status
func (h *Handler) Status(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	h.mu.RLock()
	sr := h.scoutRunning
	rr := h.researchRunning
	lsr := h.lastScoutRun
	lrr := h.lastResearchRun
	h.mu.RUnlock()

	toPtr := func(t *time.Time) *string {
		if t == nil {
			return nil
		}
		s := t.UTC().Format(time.RFC3339)
		return &s
	}

	writeJSON(w, map[string]any{
		"scout": map[string]any{
			"running":  sr,
			"last_run": toPtr(lsr),
			"schedule": "every 6h",
		},
		"research": map[string]any{
			"running":  rr,
			"last_run": toPtr(lrr),
		},
	})
}

// RunScout handles POST /api/pipeline/run/scout
// Runs the scout agent in the background, writing output to logs/scout.log.
func (h *Handler) RunScout(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	h.mu.Lock()
	if h.scoutRunning {
		h.mu.Unlock()
		writeJSON(w, map[string]any{"started": false, "message": "Scout agent already running"})
		return
	}
	h.scoutRunning = true
	h.mu.Unlock()

	go h.runAgent("Scout", filepath.Join(h.projectRoot, "agents", "scout-agent-prompt.md"), func() {
		h.mu.Lock()
		h.scoutRunning = false
		now := time.Now()
		h.lastScoutRun = &now
		h.mu.Unlock()
	})

	writeJSON(w, map[string]any{"started": true})
}

// RunResearch handles POST /api/pipeline/run/research
// Runs the research agent in the background, writing output to logs/scout.log.
func (h *Handler) RunResearch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	h.mu.Lock()
	if h.researchRunning {
		h.mu.Unlock()
		writeJSON(w, map[string]any{"started": false, "message": "Research agent already running"})
		return
	}
	h.researchRunning = true
	h.mu.Unlock()

	go h.runAgent("Research", filepath.Join(h.projectRoot, "agents", "research-agent-prompt.md"), func() {
		h.mu.Lock()
		h.researchRunning = false
		now := time.Now()
		h.lastResearchRun = &now
		h.mu.Unlock()
	})

	writeJSON(w, map[string]any{"started": true})
}

// Logs handles GET /api/pipeline/logs — returns last 50 lines of logs/scout.log.
func (h *Handler) Logs(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	logFile := filepath.Join(h.projectRoot, "logs", "scout.log")
	lines := tailFile(logFile, 50)
	writeJSON(w, map[string]any{"lines": lines})
}

// runAgent reads the prompt file at promptPath and runs `claude -p <prompt>` as a subprocess,
// appending output to logs/scout.log. onDone is called when the process exits.
func (h *Handler) runAgent(name, promptPath string, onDone func()) {
	defer onDone()

	logFile := filepath.Join(h.projectRoot, "logs", "scout.log")
	_ = os.MkdirAll(filepath.Dir(logFile), 0755)

	f, err := os.OpenFile(logFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		h.logger.Error("pipeline: cannot open log file", zap.String("agent", name), zap.Error(err))
		return
	}
	defer f.Close()

	fmt.Fprintf(f, "\n=== %s Agent — manual run triggered at %s ===\n", name, time.Now().Format(time.RFC3339))

	promptBytes, err := os.ReadFile(promptPath)
	if err != nil {
		h.logger.Error("pipeline: cannot read prompt file", zap.String("path", promptPath), zap.Error(err))
		fmt.Fprintf(f, "ERROR: cannot read prompt file %s: %v\n", promptPath, err)
		return
	}

	// claude -p "<prompt>" reads the prompt from the flag argument
	cmd := exec.Command("claude", "-p", string(promptBytes))
	cmd.Dir = h.projectRoot
	cmd.Stdout = f
	cmd.Stderr = f

	h.logger.Info("pipeline: starting agent", zap.String("agent", name))
	if err := cmd.Run(); err != nil {
		h.logger.Error("pipeline: agent run failed", zap.String("agent", name), zap.Error(err))
		fmt.Fprintf(f, "=== %s Agent FAILED: %v ===\n", name, err)
	} else {
		h.logger.Info("pipeline: agent completed", zap.String("agent", name))
		fmt.Fprintf(f, "=== %s Agent complete ===\n", name)
	}
}

func tailFile(path string, n int) []string {
	f, err := os.Open(path)
	if err != nil {
		return []string{}
	}
	defer f.Close()

	var lines []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
	}
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return lines
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
