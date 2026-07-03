import unittest
from pathlib import Path


class AdminFrontendStaticTests(unittest.TestCase):
    def test_admin_route_and_sidebar_are_wired(self):
        app_js = Path("frontend/src/App.jsx").read_text(encoding="utf-8")
        sidebar_js = Path("frontend/src/components/layout/Sidebar.jsx").read_text(encoding="utf-8")
        settings_js = Path("frontend/src/components/modals/SettingsPanel.jsx").read_text(encoding="utf-8")

        self.assertIn('path="/admin"', app_js)
        self.assertIn("AdminPage", app_js)
        self.assertIn("sessionStorage.setItem('kini_settings'", app_js)
        self.assertIn("getStoredUserRole", app_js)
        self.assertIn("ShieldCheck", sidebar_js)
        self.assertIn("path: '/admin'", sidebar_js)
        admin_js = Path("frontend/src/components/pages/AdminPage.jsx").read_text(encoding="utf-8")
        self.assertIn("/admin/users", admin_js)
        self.assertIn("Runtime status", admin_js)
        self.assertIn("Create", admin_js)
        self.assertIn("Delete", admin_js)
        self.assertIn("/memory/pending/all", admin_js)
        self.assertIn("/memory/long-term/all", admin_js)
        self.assertIn("Pending clinical memory", admin_js)
        self.assertIn("Approved clinical memory", admin_js)
        self.assertIn("Active role header", settings_js)
        self.assertIn("X-User-Role", settings_js)

    def test_partial_coverage_copy_is_not_legacy_only(self):
        quick_chat = Path("frontend/src/components/pages/QuickChat.jsx").read_text(encoding="utf-8")
        knowledge_base = Path("frontend/src/components/pages/KnowledgeBase.jsx").read_text(encoding="utf-8")

        self.assertIn("Coverage partial", quick_chat)
        self.assertIn("legacy document table", quick_chat)
        self.assertIn("source_mode", knowledge_base)
        self.assertIn("chunk_count", knowledge_base)
        self.assertIn("PageIndex", knowledge_base)
        self.assertIn("graph_nodes", knowledge_base)
        self.assertIn("graph_edges", knowledge_base)
        self.assertNotIn("legacy_documents_only", quick_chat)

    def test_release_ready_ui_controls_are_not_decorative(self):
        app_js = Path("frontend/src/App.jsx").read_text(encoding="utf-8")
        quick_chat = Path("frontend/src/components/pages/QuickChat.jsx").read_text(encoding="utf-8")
        modals = Path("frontend/src/components/modals/Modals.jsx").read_text(encoding="utf-8")
        use_chat = Path("frontend/src/hooks/useChat.js").read_text(encoding="utf-8")
        admin_page = Path("frontend/src/components/pages/AdminPage.jsx").read_text(encoding="utf-8")
        audit_page = Path("frontend/src/components/pages/AuditLog.jsx").read_text(encoding="utf-8")

        self.assertIn("key === 'p'", app_js)
        self.assertIn("setShowSettings(true)", app_js)
        self.assertIn("onShortcuts={() => setShowShortcuts(true)}", app_js)
        self.assertIn("onShortcuts(); onClose();", modals)

        self.assertIn("scopedQuery", quick_chat)
        self.assertIn("selectedDisease", quick_chat)
        self.assertIn("diseaseScope", quick_chat)

        self.assertIn("summarizeTitle", use_chat)
        self.assertIn("selectConversation", use_chat)
        self.assertIn("setMessages(conversation.messages)", use_chat)
        self.assertIn("setConversations(prev =>", use_chat)

        self.assertIn("settleAdminRequest", admin_page)
        self.assertIn("failures.map", admin_page)
        self.assertIn("storageBackend", audit_page)
        self.assertIn("backendError", audit_page)


if __name__ == "__main__":
    unittest.main()
