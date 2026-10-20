"""Entity Resolution: Group duplicate tea rooms from different sources."""
import json
import glob
import os
import re
from typing import List, Dict, Any
from rapidfuzz import fuzz
import structlog

logger = structlog.get_logger()


class EntityResolver:
    """
    Resolve duplicate tea room entities from multiple crawled sources.
    Uses fuzzy matching to group similar names into unique entities.
    """
    
    def __init__(self, similarity_threshold: float = 85.0):
        self.threshold = similarity_threshold
        self.raw_items: List[Dict[str, Any]] = []
        self.resolved_entities: List[Dict[str, Any]] = []

    def load_raw_data(self, data_dir: str = "data/raw") -> int:
        """Load all crawled JSON files from data directory."""
        self.raw_items = []
        for filepath in glob.glob(f"{data_dir}/*.json"):
            if "crawl_summary" in filepath:
                continue
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    source_name = data.get("source", "unknown")
                    for item in data.get("data", []):
                        item["_source"] = source_name
                        self.raw_items.append(item)
            except Exception as e:
                logger.error(f"Failed to load {filepath}: {e}")
        
        logger.info(f"Loaded {len(self.raw_items)} raw items from {data_dir}")
        return len(self.raw_items)

    def _clean_name(self, name: str) -> str:
        """Clean name for better fuzzy matching."""
        if not name:
            return ""
        name = re.sub(r'^\d+[\.\-\)]\s*', '', name).strip()
        name = re.sub(r'^(phòng trà|quán|cafe|coffee|lounge|bar)\s+', '', name, flags=re.IGNORECASE).strip()
        return name.lower()

    def resolve(self) -> List[Dict[str, Any]]:
        """Main resolution logic using Fuzzy Matching."""
        logger.info(f"Starting entity resolution (threshold: {self.threshold})...")
        
        clusters = []
        used_indices = set()

        for i, item in enumerate(self.raw_items):
            if i in used_indices:
                continue
            
            clean_name_i = self._clean_name(item.get("name", ""))
            if len(clean_name_i) < 3:
                continue
            
            cluster = [item]
            used_indices.add(i)

            for j, other_item in enumerate(self.raw_items):
                if j in used_indices:
                    continue
                
                clean_name_j = self._clean_name(other_item.get("name", ""))
                if len(clean_name_j) < 3:
                    continue
                
                score = fuzz.token_sort_ratio(clean_name_i, clean_name_j)
                
                if score >= self.threshold:
                    cluster.append(other_item)
                    used_indices.add(j)
            
            clusters.append(cluster)

        for cluster in clusters:
            merged_entity = self._merge_cluster(cluster)
            self.resolved_entities.append(merged_entity)

        logger.info(f"✅ Resolved {len(self.raw_items)} raw items into {len(self.resolved_entities)} unique entities")
        return self.resolved_entities

    def _merge_cluster(self, cluster: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge multiple records of the same tea room into one rich entity."""
        primary_item = max(cluster, key=lambda x: len(str(x.get("description", ""))))
        
        sources = []
        for item in cluster:
            sources.append({
                "domain": item.get("_source", "unknown"),
                "raw_name": item.get("name", "")
            })
        
        def get_best(field: str):
            return max([str(item.get(field, "")).strip() for item in cluster], key=len)

        # Merge all images from all sources
        all_images = []
        for item in cluster:
            if item.get("image_urls"):
                all_images.extend(item["image_urls"])
            elif item.get("image_url"):
                all_images.append(item["image_url"])
        
        unique_images = []
        seen = set()
        for img in all_images:
            if img and img.startswith("http") and img not in seen:
                unique_images.append(img)
                seen.add(img)
        
        best_image = unique_images[0] if unique_images else ""

        return {
            "name": primary_item.get("name", "").strip(),
            "aliases": list(set([
                item.get("name", "").strip() 
                for item in cluster 
                if item.get("name", "").strip() != primary_item.get("name", "").strip()
            ])),
            "image_url": best_image,
            "image_urls": unique_images[:5],
            "address": get_best("address"),
            "price": get_best("price"),
            "hours": get_best("hours"),
            "description": get_best("description")[:1000],
            "sources": sources,
            "source_count": len(sources)
        }

    def save_resolved_data(self, output_path: str = "data/processed/resolved_entities.json"):
        """Save resolved entities to JSON file."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.resolved_entities, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ Saved {len(self.resolved_entities)} resolved entities to {output_path}")


if __name__ == "__main__":
    resolver = EntityResolver(similarity_threshold=85.0)
    count = resolver.load_raw_data()
    
    if count > 0:
        resolver.resolve()
        resolver.save_resolved_data()
        
        print("\n" + "=" * 60)
        print("ENTITY RESOLUTION SUMMARY")
        print("=" * 60)
        print(f"Raw items: {count}")
        print(f"Unique entities: {len(resolver.resolved_entities)}")
        
        with_images = sum(1 for e in resolver.resolved_entities if e.get("image_url"))
        print(f"With images: {with_images}/{len(resolver.resolved_entities)} ({with_images*100//len(resolver.resolved_entities)}%)")
        
        sorted_entities = sorted(resolver.resolved_entities, key=lambda x: x["source_count"], reverse=True)
        print(f"\nTop 5 entities by source count:")
        for entity in sorted_entities[:5]:
            print(f"  - {entity['name']} ({entity['source_count']} sources)")
            if entity.get("aliases"):
                print(f"    Aliases: {', '.join(entity['aliases'][:3])}")
        
        print("=" * 60)
    else:
        logger.warning("No raw data found. Please run the crawler first.")