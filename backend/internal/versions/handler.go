// Package versions exposes the HTTP API for Docker image version management.
package versions

import (
	"bufio"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/marketflow/backend/internal/db"
)

const (
	historyFile = "/app/versions/history"
	currentFile = "/app/versions/current"
	switchFile  = "/app/versions/switch-request"
	notesDir    = "/app/versions/notes"
)

// Handler holds the DB dependency for version-label persistence.
type Handler struct {
	db *db.DB
}

// New builds a version Handler backed by the given database.
func New(database *db.DB) *Handler {
	return &Handler{db: database}
}

// Version represents one deployed build.
type Version struct {
	Tag       string `json:"tag"`
	Timestamp string `json:"timestamp"`
	GitSHA    string `json:"git_sha"`
	Note      string `json:"note"`
	Label     string `json:"label"`
	Active    bool   `json:"active"`
}

// List returns all recorded versions with the active one marked.
// GET /api/versions
func (h *Handler) List(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	current := strings.TrimSpace(readFile(currentFile))
	labels, err := h.db.ListVersionLabels()
	if err != nil {
		labels = map[string]string{}
	}
	versions := parseHistory(historyFile, current, labels)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"versions": versions,
		"current":  current,
	})
}

// Switch writes a switch request for the host watcher to pick up.
// POST /api/versions/switch  { "version": "20260524-2126" }
func (h *Handler) Switch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		Version string `json:"version"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Version == "" {
		http.Error(w, "version is required", http.StatusBadRequest)
		return
	}

	if err := os.WriteFile(switchFile, []byte(req.Version), 0644); err != nil {
		http.Error(w, "failed to write switch request", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"switching": true,
		"version":   req.Version,
	})
}

// UpdateNote saves a description for a specific version.
// PATCH /api/versions/{version}/note  { "note": "some text" }
func (h *Handler) UpdateNote(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPatch {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	version := r.PathValue("version")
	if version == "" || strings.Contains(version, "..") {
		http.Error(w, "invalid version", http.StatusBadRequest)
		return
	}

	var req struct {
		Note string `json:"note"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid body", http.StatusBadRequest)
		return
	}

	_ = os.MkdirAll(notesDir, 0755)
	notePath := filepath.Join(notesDir, version)
	if err := os.WriteFile(notePath, []byte(strings.TrimSpace(req.Note)), 0644); err != nil {
		http.Error(w, "failed to save note", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"success": true})
}

// UpdateLabel saves a user-defined tag for a specific version (e.g. "WORKING", "STABLE").
// Persisted in the version_labels DB table so it survives independently of the deploy history files.
// PATCH /api/versions/{version}/label  { "label": "WORKING" }
func (h *Handler) UpdateLabel(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPatch {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	version := r.PathValue("version")
	if version == "" || strings.Contains(version, "..") {
		http.Error(w, "invalid version", http.StatusBadRequest)
		return
	}

	var req struct {
		Label string `json:"label"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid body", http.StatusBadRequest)
		return
	}

	if err := h.db.UpsertVersionLabel(version, strings.TrimSpace(req.Label)); err != nil {
		http.Error(w, "failed to save tag", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"success": true})
}

// ─── helpers ──────────────────────────────────────────────────────────────────

func parseHistory(path, current string, labels map[string]string) []Version {
	f, err := os.Open(path)
	if err != nil {
		return []Version{}
	}
	defer f.Close()

	var versions []Version
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		parts := strings.Fields(line)
		v := Version{Tag: parts[0]}
		if len(parts) >= 3 {
			v.Timestamp = parts[1] + " " + parts[2]
		}
		if len(parts) >= 4 {
			v.GitSHA = parts[3]
		}
		v.Note = strings.TrimSpace(readFile(filepath.Join(notesDir, v.Tag)))
		v.Label = labels[v.Tag]
		v.Active = v.Tag == current
		versions = append(versions, v)
	}

	// Return newest first
	for i, j := 0, len(versions)-1; i < j; i, j = i+1, j-1 {
		versions[i], versions[j] = versions[j], versions[i]
	}
	return versions
}

func readFile(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(b)
}
