// Package versions exposes the HTTP API for Docker image version management.
package versions

import (
	"bufio"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

const (
	historyFile = "/app/versions/history"
	currentFile = "/app/versions/current"
	switchFile  = "/app/versions/switch-request"
	notesDir    = "/app/versions/notes"
)

// Version represents one deployed build.
type Version struct {
	Tag       string `json:"tag"`
	Timestamp string `json:"timestamp"`
	GitSHA    string `json:"git_sha"`
	Note      string `json:"note"`
	Active    bool   `json:"active"`
}

// List returns all recorded versions with the active one marked.
// GET /api/versions
func List(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	current := strings.TrimSpace(readFile(currentFile))
	versions := parseHistory(historyFile, current)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"versions": versions,
		"current":  current,
	})
}

// Switch writes a switch request for the host watcher to pick up.
// POST /api/versions/switch  { "version": "20260524-2126" }
func Switch(w http.ResponseWriter, r *http.Request) {
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
func UpdateNote(w http.ResponseWriter, r *http.Request) {
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

// ─── helpers ──────────────────────────────────────────────────────────────────

func parseHistory(path, current string) []Version {
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
