"""Auto-generate knowledge links between cards based on semantic similarity, tags, and domain."""

from __future__ import annotations

import json
import logging
import re
from uuid import uuid4

from app.core.database import get_db_connection

logger = logging.getLogger("app.knowledge_links")

SIMILARITY_THRESHOLD = 0.75
SAME_TAG_WEIGHT = 0.1
SAME_DOMAIN_WEIGHT = 0.15
WIKI_LINK_WEIGHT = 0.3


class KnowledgeLinkService:
    """Generates and manages links between knowledge cards."""

    def auto_link_card(self, card_id: str, project_id: str) -> int:
        """Generate links for a newly ingested card. Returns number of links created."""
        with get_db_connection() as conn:
            card = conn.execute(
                "SELECT id, title, knowledge_domain, tags "
                "FROM knowledge_cards WHERE id = %s",
                (card_id,),
            ).fetchone()
            if not card:
                return 0

            # 1. Semantic similarity links (embedding-based)
            semantic_links = self._find_semantic_neighbors(conn, card, project_id)

            # 2. Tag-based links
            tag_links = self._find_tag_siblings(conn, card, project_id)

            # 3. Domain neighbors
            domain_links = self._find_domain_neighbors(conn, card, project_id)

            # 4. Wiki-style [[links]] in content
            wiki_links = self._find_wiki_refs(conn, card_id, project_id)

            # Merge and deduplicate
            all_links: dict[str, dict] = {}
            for target_id, score, relation in semantic_links + tag_links + domain_links + wiki_links:
                key = f"{card_id}:{target_id}"
                if key not in all_links or all_links[key]["score"] < score:
                    all_links[key] = {
                        "id": f"lk_{uuid4().hex[:12]}",
                        "source_card_id": card_id,
                        "target_card_id": target_id,
                        "relation": relation,
                        "score": round(score, 4),
                    }

            # Insert links
            created = 0
            for link in all_links.values():
                try:
                    conn.execute(
                        """INSERT INTO knowledge_links (id, source_card_id, target_card_id, relation, score)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (source_card_id, target_card_id, relation) DO UPDATE
                           SET score = EXCLUDED.score""",
                        (link["id"], link["source_card_id"], link["target_card_id"],
                         link["relation"], link["score"]),
                    )
                    created += 1
                except Exception as e:
                    logger.debug("link skip: %s", e)

            # Update linked_card_ids on the source card
            linked_ids = [l["target_card_id"] for l in all_links.values()]
            conn.execute(
                "UPDATE knowledge_cards SET linked_card_ids = %s WHERE id = %s",
                (json.dumps(linked_ids), card_id),
            )
            conn.commit()
            logger.info("auto-linked card %s: %d links created", card_id, created)
            return created

    def get_related_cards(self, card_id: str, limit: int = 5) -> list[dict]:
        """Get related cards for a given card."""
        with get_db_connection() as conn:
            rows = conn.execute(
                """SELECT kl.target_card_id, kl.relation, kl.score,
                          kc.title, kc.knowledge_domain, kc.tags
                   FROM knowledge_links kl
                   JOIN knowledge_cards kc ON kc.id = kl.target_card_id
                   WHERE kl.source_card_id = %s
                   ORDER BY kl.score DESC
                   LIMIT %s""",
                (card_id, limit),
            ).fetchall()
            return [
                {
                    "card_id": r["target_card_id"],
                    "title": r["title"],
                    "domain": r["knowledge_domain"],
                    "relation": r["relation"],
                    "score": float(r["score"]),
                    "tags": json.loads(r["tags"]) if r["tags"] else [],
                }
                for r in rows
            ]

    def get_graph(self, project_id: str, center_card_id: str | None = None, depth: int = 2) -> dict:
        """Get knowledge graph for visualization."""
        with get_db_connection() as conn:
            # Get all cards in project
            cards = conn.execute(
                "SELECT id, title, knowledge_domain, tags FROM knowledge_cards "
                "WHERE project_id = %s AND status = 'active'",
                (project_id,),
            ).fetchall()

            # Get all links
            card_ids = [c["id"] for c in cards]
            if not card_ids:
                return {"nodes": [], "edges": []}

            placeholders = ",".join(["%s"] * len(card_ids))
            links = conn.execute(
                f"""SELECT source_card_id, target_card_id, relation, score
                    FROM knowledge_links
                    WHERE source_card_id IN ({placeholders})""",
                card_ids,
            ).fetchall()

            nodes = [
                {"id": c["id"], "title": c["title"], "domain": c["knowledge_domain"],
                 "tags": json.loads(c["tags"]) if c["tags"] else []}
                for c in cards
            ]
            edges = [
                {"source": l["source_card_id"], "target": l["target_card_id"],
                 "relation": l["relation"], "score": float(l["score"])}
                for l in links
            ]
            return {"nodes": nodes, "edges": edges}

    def _find_semantic_neighbors(self, conn, card, project_id: str) -> list[tuple]:
        """Find cards with similar embeddings via chunk vectors."""
        # Get average embedding for the source card's chunks
        src = conn.execute(
            """SELECT AVG(embedding_vec)::vector AS avg_emb
               FROM knowledge_card_chunks
               WHERE card_id = %s AND embedding_vec IS NOT NULL""",
            (card["id"],),
        ).fetchone()
        if not src or not src["avg_emb"]:
            return []

        # Find other cards by average chunk similarity
        rows = conn.execute(
            """SELECT kcc.card_id AS id,
                       1 - (AVG(kcc.embedding_vec) <=> %s::vector) AS similarity
               FROM knowledge_card_chunks kcc
               JOIN knowledge_cards kc ON kc.id = kcc.card_id
               WHERE kc.project_id = %s AND kc.id != %s AND kc.status = 'active'
                 AND kcc.embedding_vec IS NOT NULL
               GROUP BY kcc.card_id
               ORDER BY AVG(kcc.embedding_vec) <=> %s::vector
               LIMIT 10""",
            (src["avg_emb"], project_id, card["id"], src["avg_emb"]),
        ).fetchall()
        return [
            (r["id"], float(r["similarity"]), "related")
            for r in rows
            if float(r["similarity"]) >= SIMILARITY_THRESHOLD
        ]

    def _find_tag_siblings(self, conn, card, project_id: str) -> list[tuple]:
        """Find cards with overlapping tags."""
        tags = json.loads(card["tags"]) if card["tags"] else []
        if not tags:
            return []
        rows = conn.execute(
            """SELECT id, tags FROM knowledge_cards
               WHERE project_id = %s AND id != %s AND status = 'active'""",
            (project_id, card["id"]),
        ).fetchall()
        results = []
        for r in rows:
            other_tags = json.loads(r["tags"]) if r["tags"] else []
            overlap = len(set(tags) & set(other_tags))
            if overlap > 0:
                score = min(0.5 + overlap * SAME_TAG_WEIGHT, 0.9)
                results.append((r["id"], score, "sibling"))
        return results

    def _find_domain_neighbors(self, conn, card, project_id: str) -> list[tuple]:
        """Find cards in the same domain."""
        domain = card["knowledge_domain"]
        if not domain:
            return []
        rows = conn.execute(
            """SELECT id FROM knowledge_cards
               WHERE project_id = %s AND id != %s AND status = 'active'
                 AND knowledge_domain = %s""",
            (project_id, card["id"], domain),
        ).fetchall()
        return [(r["id"], SAME_DOMAIN_WEIGHT + 0.3, "neighbor") for r in rows]

    def _find_wiki_refs(self, conn, card_id: str, project_id: str) -> list[tuple]:
        """Parse [[wiki-links]] in card content and find referenced cards."""
        card = conn.execute(
            "SELECT content FROM knowledge_cards WHERE id = %s", (card_id,)
        ).fetchone()
        if not card:
            return []
        content = card["content"] or ""
        refs = re.findall(r"\[\[([^\]]+)\]\]", content)
        if not refs:
            return []
        results = []
        for ref in refs:
            target = conn.execute(
                "SELECT id FROM knowledge_cards WHERE project_id = %s AND title ILIKE %s",
                (project_id, f"%{ref}%"),
            ).fetchone()
            if target:
                results.append((target["id"], WIKI_LINK_WEIGHT + 0.5, "reference"))
        return results
