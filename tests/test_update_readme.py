"""Suite de pruebas para el script que actualiza el README con datos de League of Legends.

Estas pruebas se enfocan en validar la lógica de formateo de tiempos, la construcción
 del bloque de Markdown y la sustitución del contenido marcado dentro del README.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import update_readme


class FormatTimeDeltaTests(unittest.TestCase):
    """Pruebas para el formateo de tiempos relativos."""

    def test_formats_recent_time(self):
        """Verifica que un tiempo reciente se convierta a horas y minutos."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=3, minutes=25)
        self.assertEqual(update_readme.format_time_since(past, now), "3h 25m ago")

    def test_formats_days_and_hours(self):
        """Verifica que un tiempo más largo se represente con días y horas."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=2, hours=6)
        self.assertEqual(update_readme.format_time_since(past, now), "2d 6h ago")

    def test_returns_never_when_none(self):
        """Verifica que un valor nulo se traduzca a un texto claro."""
        self.assertEqual(update_readme.format_time_since(None), "Never")

    def test_returns_just_now_for_immediate_differences(self):
        """Verifica que un tiempo casi inmediato se etiquete como "just now"."""
        now = datetime.now(timezone.utc)
        self.assertEqual(update_readme.format_time_since(now, now), "just now")


class LastMatchAgeTests(unittest.TestCase):
    """Pruebas para detectar la antigüedad de la última partida."""

    @patch("update_readme.get_match_ids")
    def test_returns_never_when_no_matches_exist(self, mock_get_match_ids):
        """Si no hay partidas, la función debe informar que no existe registro."""
        mock_get_match_ids.return_value = []
        self.assertEqual(update_readme.get_last_match_age("puuid", 420), "Never")

    @patch("update_readme.get_match_ids")
    @patch("update_readme.riot_get")
    def test_returns_human_readable_age_from_last_game(self, mock_riot_get, mock_get_match_ids):
        """Verifica que la función devuelva una cadena legible con la antigüedad."""
        mock_get_match_ids.return_value = ["match-1"]
        played_at = datetime.now(timezone.utc) - timedelta(hours=1, minutes=30)
        mock_riot_get.return_value = {"info": {"gameEndTimestamp": int(played_at.timestamp() * 1000)}}

        self.assertEqual(update_readme.get_last_match_age("puuid", 420), "1h 30m ago")


class BuildMarkdownTests(unittest.TestCase):
    """Pruebas para la creación del bloque de Markdown del README."""

    def test_build_markdown_includes_stats_and_last_played_fields(self):
        """Verifica que el bloque generado incluya los datos esperados."""
        block = update_readme.build_markdown(
            "Karthus",
            {"championLevel": 7, "championPoints": 12345},
            {"tier": "gold", "rank": "I", "leaguePoints": 38, "wins": 25, "losses": 19},
            12,
            3,
            80.0,
            15,
            8,
            2,
            80.0,
            10,
            "2h ago",
            "3d ago",
        )

        self.assertIn("### 📊 Stats of Karthus", block)
        self.assertIn("- **Maestry:** Nivel 7 — 12,345 puntos", block)
        self.assertIn("- **Rank (SoloQ):** Gold I — 38 LP (25W / 19L, 56.8% WR)", block)
        self.assertIn("- **Last Ranked Game:** 2h ago", block)
        self.assertIn("- **Last Normal Game:** 3d ago", block)


class UpdateReadmeTests(unittest.TestCase):
    """Pruebas para la sustitución del bloque marcado dentro del README."""

    def test_update_readme_replaces_the_marked_block(self):
        """Verifica que el contenido entre los marcadores se sustituya correctamente."""
        with tempfile.TemporaryDirectory() as tmpdir:
            readme_path = os.path.join(tmpdir, "README.md")
            with open(readme_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "# Title\n"
                    "before\n"
                    "<!---LOL-STATS-START-HERE--->\n"
                    "old block\n"
                    "<!---LOL-STATS-END-HERE--->\n"
                    "after\n"
                )

            with patch.object(update_readme, "README_PATH", readme_path):
                update_readme.update_readme("new block")

            with open(readme_path, "r", encoding="utf-8") as handle:
                content = handle.read()

        self.assertIn("before\n<!---LOL-STATS-START-HERE--->\nnew block\n<!---LOL-STATS-END-HERE--->\nafter", content)
        self.assertNotIn("old block", content)


if __name__ == "__main__":
    unittest.main()
