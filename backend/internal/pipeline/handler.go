// Package pipeline exposes the HTTP API for the Repo Scout & Research pipeline.
package pipeline

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"go.uber.org/zap"

	"github.com/marketflow/backend/internal/db"
)

// Handler holds shared state for all pipeline endpoints.
type Handler struct {
	mu              sync.RWMutex
	scoutRunning    bool
	researchRunning bool
	lastScoutRun    *time.Time
	lastResearchRun *time.Time

	projectRoot string
	db          *db.DB
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

// New creates a Handler using the existing SQLite DB — no external services required.
func New(projectRoot string, database *db.DB, logger *zap.Logger) *Handler {
	return &Handler{
		projectRoot: projectRoot,
		db:          database,
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
	statusFilter := r.URL.Query().Get("status")
	rows, err := h.db.ListRepos(statusFilter)
	if err != nil {
		writeJSON(w, map[string]any{"repos": []any{}, "error": err.Error()})
		return
	}
	if rows == nil {
		rows = []map[string]any{}
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

	var req struct{ Model string `json:"model"` }
	_ = json.NewDecoder(r.Body).Decode(&req)

	h.mu.Lock()
	if h.scoutRunning {
		h.mu.Unlock()
		writeJSON(w, map[string]any{"started": false, "message": "Scout agent already running"})
		return
	}
	h.scoutRunning = true
	h.mu.Unlock()

	go h.runAgent("Scout", filepath.Join(h.projectRoot, "agents", "scout-agent-prompt.md"), req.Model, func() {
		h.mu.Lock()
		h.scoutRunning = false
		now := time.Now()
		h.lastScoutRun = &now
		h.mu.Unlock()
	})

	writeJSON(w, map[string]any{"started": true, "model": req.Model})
}

// RunResearch handles POST /api/pipeline/run/research
// Runs the research agent in the background, writing output to logs/scout.log.
func (h *Handler) RunResearch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct{ Model string `json:"model"` }
	_ = json.NewDecoder(r.Body).Decode(&req)

	h.mu.Lock()
	if h.researchRunning {
		h.mu.Unlock()
		writeJSON(w, map[string]any{"started": false, "message": "Research agent already running"})
		return
	}
	h.researchRunning = true
	h.mu.Unlock()

	go h.runAgent("Research", filepath.Join(h.projectRoot, "agents", "research-agent-prompt.md"), req.Model, func() {
		h.mu.Lock()
		h.researchRunning = false
		now := time.Now()
		h.lastResearchRun = &now
		h.mu.Unlock()
	})

	writeJSON(w, map[string]any{"started": true, "model": req.Model})
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
func (h *Handler) runAgent(name, promptPath, model string, onDone func()) {
	defer onDone()

	logFile := filepath.Join(h.projectRoot, "logs", "scout.log")
	_ = os.MkdirAll(filepath.Dir(logFile), 0755)

	f, err := os.OpenFile(logFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		h.logger.Error("pipeline: cannot open log file", zap.String("agent", name), zap.Error(err))
		return
	}
	defer f.Close()

	fmt.Fprintf(f, "\n=== %s Agent — manual run triggered at %s (model: %s) ===\n", name, time.Now().Format(time.RFC3339), model)

	promptBytes, err := os.ReadFile(promptPath)
	if err != nil {
		h.logger.Error("pipeline: cannot read prompt file", zap.String("path", promptPath), zap.Error(err))
		fmt.Fprintf(f, "ERROR: cannot read prompt file %s: %v\n", promptPath, err)
		return
	}

	claudePath, lookErr := exec.LookPath("claude")
	if lookErr != nil {
		// LookPath only searches PATH; fall back to common install locations when the
		// server is launched outside a full user shell (e.g. launchd, systemd).
		for _, candidate := range []string{
			filepath.Join(os.Getenv("HOME"), ".local", "bin", "claude"),
			"/usr/local/bin/claude",
			"/opt/homebrew/bin/claude",
		} {
			if _, statErr := os.Stat(candidate); statErr == nil {
				claudePath = candidate
				break
			}
		}
	}
	if claudePath == "" {
		h.logger.Error("pipeline: claude CLI not found", zap.String("agent", name))
		fmt.Fprintf(f, "=== %s Agent FAILED: claude CLI not found in PATH or common locations ===\n", name)
		return
	}

	cliModel := mapModelToCLI(model)
	args := []string{"-p", string(promptBytes)}
	if cliModel != "" {
		args = append([]string{"--model", cliModel}, args...)
	}
	cmd := exec.Command(claudePath, args...)
	cmd.Dir = h.projectRoot
	cmd.Stdout = f
	cmd.Stderr = f

	h.logger.Info("pipeline: starting agent", zap.String("agent", name), zap.String("model", model))
	if err := cmd.Run(); err != nil {
		h.logger.Error("pipeline: agent run failed", zap.String("agent", name), zap.Error(err))
		fmt.Fprintf(f, "=== %s Agent FAILED: %v ===\n", name, err)
	} else {
		h.logger.Info("pipeline: agent completed", zap.String("agent", name))
		fmt.Fprintf(f, "=== %s Agent complete ===\n", name)
	}
}

var cliModelMap = map[string]string{
	"claude-sonnet": "claude-sonnet-4-6",
}

// mapModelToCLI returns the Claude Code CLI model ID for known Claude models.
// Non-Claude models (Ollama) return "" — the CLI uses its default.
func mapModelToCLI(uiModel string) string {
	if m, ok := cliModelMap[uiModel]; ok {
		return m
	}
	if strings.HasPrefix(uiModel, "claude") {
		return uiModel
	}
	return ""
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
