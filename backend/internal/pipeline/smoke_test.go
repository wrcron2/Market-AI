package pipeline

// Guarded end-to-end smoke test for the native Scout/Research agents.
// Skipped unless SCOUT_SMOKE=1 — it hits GitHub and a real LLM provider.
//
//	set -a; source ../../.env; set +a
//	SCOUT_SMOKE=1 go test ./internal/pipeline -run TestScoutSmoke -v -timeout 15m

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.uber.org/zap"

	"github.com/marketflow/backend/internal/db"
)

func TestScoutSmoke(t *testing.T) {
	if os.Getenv("SCOUT_SMOKE") != "1" {
		t.Skip("set SCOUT_SMOKE=1 to run (hits GitHub + LLM APIs)")
	}

	root := t.TempDir()
	database, err := db.Open(filepath.Join(root, "smoke.db"))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	if err := database.Migrate(); err != nil {
		t.Fatalf("migrate: %v", err)
	}

	h := New(root, database, zap.NewNop())

	model := os.Getenv("SCOUT_SMOKE_MODEL")
	if model == "" {
		model = "deepseek-r1" // Groq with Ollama fallback
	}

	h.runScout(model)

	logBytes, _ := os.ReadFile(filepath.Join(root, "logs", "scout.log"))
	logText := string(logBytes)
	t.Logf("scout.log:\n%s", logText)

	if !strings.Contains(logText, "=== Scout Agent complete") {
		t.Fatalf("scout did not complete cleanly")
	}

	repos, err := database.ListRepos("")
	if err != nil {
		t.Fatalf("list repos: %v", err)
	}
	t.Logf("repos stored: %d", len(repos))
	if len(repos) == 0 {
		t.Fatalf("expected scout to store at least one repo")
	}

	// Research pass: only if the scout marked something good.
	if goodRepos, _ := database.ListRepos("good"); len(goodRepos) > 0 {
		h.runResearch(model)
		logBytes, _ = os.ReadFile(filepath.Join(root, "logs", "scout.log"))
		logText = string(logBytes)
		t.Logf("scout.log after research:\n%s", logText)
		if !strings.Contains(logText, "=== Research Agent complete") {
			t.Fatalf("research did not complete cleanly")
		}
		researched, _ := database.ListRepos("researched")
		if len(researched) == 0 {
			t.Fatalf("expected at least one researched repo with a report")
		}
		_, report, err := database.GetResearchReport(researched[0]["id"].(int64))
		if err != nil || len(report) < 200 {
			t.Fatalf("stored report looks wrong (err=%v, len=%d)", err, len(report))
		}
		t.Logf("first report (%d chars):\n%.600s…", len(report), report)
	}
}
