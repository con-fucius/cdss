"""
RAG Qdrant: Qdrant-based vector retrieval with multi-entity extraction,
relationship retrieval, and drug-drug interaction mapping
Uses existing Qdrant collection with UMLS concept embeddings
"""
from typing import List, Dict, Tuple, Optional, Set
from api.services.rag.base_rag import BaseRAGService
from api.config import settings
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from api.db import SessionLocal
from api.models.umls import UMLSConcept, UMLSRelation
import logging
import re

logger = logging.getLogger(__name__)


class RAGServiceQdrant(BaseRAGService):
    """RAG implementation using Qdrant vector database"""
    
    def __init__(self):
        self.client = None
        self.embedding_model = None
        self.collection_name = settings.QDRANT_COLLECTION_NAME
        self._initialize()
    
    def _initialize(self):
        """Initialize Qdrant client and embedding model"""
        try:
            # Initialize Qdrant client
            if settings.QDRANT_API_KEY:
                self.client = QdrantClient(
                    url=settings.QDRANT_URL,
                    api_key=settings.QDRANT_API_KEY
                )
            else:
                self.client = QdrantClient(url=settings.QDRANT_URL)
            
            # Load embedding model (same as used for indexing)
            logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
            self.embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL)
            
            # Verify collection exists (with error handling for version mismatches)
            try:
                collections = self.client.get_collections()
                collection_exists = any(c.name == self.collection_name for c in collections.collections)
                
                if not collection_exists:
                    logger.warning(f"Collection '{self.collection_name}' does not exist in Qdrant")
                else:
                    # Try to get collection info, but handle version mismatch errors
                    try:
                        collection_info = self.client.get_collection(self.collection_name)
                        logger.info(f"✓ Connected to Qdrant. Collection '{self.collection_name}' has {collection_info.points_count:,} points")
                    except Exception as info_error:
                        # Version mismatch - collection exists but can't read full info
                        logger.warning(f"Collection '{self.collection_name}' exists but couldn't read info (version mismatch): {info_error}")
                        logger.info(f"✓ Connected to Qdrant. Collection '{self.collection_name}' is available")
            except Exception as coll_error:
                logger.warning(f"Could not verify collection existence: {coll_error}")
                logger.info("Continuing anyway - collection may be accessible")
                
        except Exception as e:
            logger.error(f"Error initializing Qdrant RAG service: {e}", exc_info=True)
            # Reset to None on error
            self.client = None
            self.embedding_model = None
            logger.warning("Qdrant RAG service initialization failed - RAG will not work until fixed")
    
    def _extract_entities(self, query: str) -> List[str]:
        """
        Extract potential entity mentions from query for multi-entity search
        Simple approach: split on common separators and filter
        """
        # Split on common separators
        entities = re.split(r'[,;]\s*|\s+and\s+|\s+with\s+|\s+between\s+', query.lower())
        # Clean and filter
        entities = [e.strip() for e in entities if len(e.strip()) > 2]
        # Remove common stop words
        stop_words = {'the', 'for', 'check', 'interaction', 'interactions', 'drug', 'drugs', 'of', 'in', 'on', 'at', 'to'}
        entities = [e for e in entities if e not in stop_words]
        
        # If we have multiple entities, return them; otherwise return the full query
        if len(entities) > 1:
            return entities
        elif len(entities) == 1:
            # Try to extract drug names or medical terms (simple heuristic: words that look like proper nouns/medical terms)
            # For now, just return the single entity and the full query
            return [entities[0], query]
        else:
            # Fallback: return the original query
            return [query]
    
    def _is_drug(self, semantic_types: List[str]) -> bool:
        """Check if concept is a drug/pharmacologic substance"""
        drug_types = [
            "Pharmacologic Substance",
            "T121",  # Pharmacologic Substance
            "T200",  # Clinical Drug
            "Organic Chemical"
        ]
        return any(dt in str(st) for st in semantic_types for dt in drug_types)
    
    async def _get_relations(
        self,
        cuis: List[str],
        relation_types: Optional[List[str]] = None
    ) -> List[Dict[str, str]]:
        """Retrieve UMLS relations for given CUIs from PostgreSQL"""
        if not cuis:
            return []
        
        db = SessionLocal()
        try:
            # Query relations where either cui1 or cui2 is in our list
            query = db.query(UMLSRelation).filter(
                (UMLSRelation.cui1.in_(cuis)) | (UMLSRelation.cui2.in_(cuis))
            )
            
            if relation_types:
                query = query.filter(UMLSRelation.relation_type.in_(relation_types))
            
            relations = query.limit(100).all()
            
            # Get concept names for better context
            all_cuis = set()
            for rel in relations:
                all_cuis.add(rel.cui1)
                all_cuis.add(rel.cui2)
            
            # Fetch concept names
            concepts = db.query(UMLSConcept).filter(UMLSConcept.cui.in_(list(all_cuis))).all()
            cui_to_name = {c.cui: c.preferred_name for c in concepts}
            
            # Format relations
            formatted_relations = []
            for rel in relations:
                formatted_relations.append({
                    "cui1": rel.cui1,
                    "cui2": rel.cui2,
                    "concept1": cui_to_name.get(rel.cui1, rel.cui1),
                    "concept2": cui_to_name.get(rel.cui2, rel.cui2),
                    "relation_type": rel.relation_type,
                    "relation_label": rel.relation_label or "",
                    "text": f"{cui_to_name.get(rel.cui1, rel.cui1)} {rel.relation_label or rel.relation_type} {cui_to_name.get(rel.cui2, rel.cui2)}"
                })
            
            return formatted_relations
        except Exception as e:
            logger.error(f"Error retrieving relations: {e}", exc_info=True)
            return []
        finally:
            db.close()
    
    async def _get_drug_interactions(
        self,
        drug_cuis: List[str]
    ) -> List[Dict[str, str]]:
        """Get drug-drug interactions between drug concepts"""
        if len(drug_cuis) < 2:
            return []
        
        db = SessionLocal()
        try:
            # Find relations between drug concepts
            # Check both directions: cui1->cui2 and cui2->cui1
            interactions = []
            
            for i, cui1 in enumerate(drug_cuis):
                for cui2 in drug_cuis[i+1:]:
                    # Check both directions
                    relations = db.query(UMLSRelation).filter(
                        ((UMLSRelation.cui1 == cui1) & (UMLSRelation.cui2 == cui2)) |
                        ((UMLSRelation.cui1 == cui2) & (UMLSRelation.cui2 == cui1))
                    ).all()
                    
                    for rel in relations:
                        # Get concept names
                        c1 = db.query(UMLSConcept).filter(UMLSConcept.cui == cui1).first()
                        c2 = db.query(UMLSConcept).filter(UMLSConcept.cui == cui2).first()
                        
                        interactions.append({
                            "cui1": cui1,
                            "cui2": cui2,
                            "concept1": c1.preferred_name if c1 else cui1,
                            "concept2": c2.preferred_name if c2 else cui2,
                            "relation_type": rel.relation_type,
                            "relation_label": rel.relation_label or "",
                            "text": f"{c1.preferred_name if c1 else cui1} {rel.relation_label or rel.relation_type} {c2.preferred_name if c2 else cui2}",
                            "interaction_type": "drug_drug"
                        })
            
            return interactions
        except Exception as e:
            logger.error(f"Error retrieving drug interactions: {e}", exc_info=True)
            return []
        finally:
            db.close()
    
    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        **kwargs
    ) -> Tuple[List[Dict[str, str]], List[str]]:
        """
        Retrieve relevant concepts using:
        1. Multi-entity extraction and similarity search
        2. Relationship retrieval
        3. Drug-drug interaction mapping
        
        Returns:
            Tuple of (context_documents, umls_concepts)
        """
        try:
            # Ensure embedding model is loaded
            if not self.embedding_model:
                logger.warning("Embedding model not loaded, loading now...")
                try:
                    self.embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL)
                    logger.info("Embedding model loaded successfully")
                except Exception as e:
                    logger.error(f"Failed to load embedding model: {e}")
                    return [], []
            
            # STEP 1: Multi-entity extraction
            entities = self._extract_entities(query)
            logger.info(f"Extracted {len(entities)} entities from query: {entities}")
            
            # Create fresh Qdrant client
            try:
                if settings.QDRANT_API_KEY:
                    client = QdrantClient(
                        url=settings.QDRANT_URL,
                        api_key=settings.QDRANT_API_KEY
                    )
                else:
                    client = QdrantClient(url=settings.QDRANT_URL)
            except Exception as client_err:
                logger.error(f"Failed to create Qdrant client: {client_err}")
                return [], []
            
            # STEP 2: Multi-entity similarity search
            all_results = []
            all_cuis = set()
            concept_details = {}  # cui -> {preferred_name, semantic_types, text}
            
            # Always do full query search first (most reliable)
            try:
                logger.info(f"Searching Qdrant for full query: '{query}'")
                query_embedding = self.embedding_model.encode([query], show_progress_bar=False)[0]
                logger.debug(f"Query embedding dimension: {len(query_embedding)}")
                
                # Try search method first, fallback to query_points or HTTP
                full_query_results = []
                if hasattr(client, 'search'):
                    try:
                        full_query_results = client.search(
                            collection_name=self.collection_name,
                            query_vector=query_embedding.tolist(),
                            limit=top_k * 2,
                            with_payload=True,
                            with_vectors=False
                        )
                    except AttributeError:
                        logger.warning("client.search() failed, trying query_points...")
                        full_query_results = []
                
                # Fallback to query_points if search doesn't work
                if not full_query_results and hasattr(client, 'query_points'):
                    try:
                        from qdrant_client.models import Query, QueryFilter
                        query_obj = Query(
                            vector=query_embedding.tolist(),
                            top=top_k * 2,
                            with_payload=True,
                            with_vectors=False
                        )
                        query_result = client.query_points(
                            collection_name=self.collection_name,
                            query=query_obj
                        )
                        full_query_results = query_result.points if hasattr(query_result, 'points') else []
                    except Exception as qp_err:
                        logger.warning(f"query_points also failed: {qp_err}")
                        full_query_results = []
                
                # Final fallback: HTTP API
                if not full_query_results:
                    logger.warning("Using HTTP API fallback for search")
                    import httpx
                    response = httpx.post(
                        f"{settings.QDRANT_URL}/collections/{self.collection_name}/points/search",
                        json={
                            "vector": query_embedding.tolist(),
                            "limit": top_k * 2,
                            "with_payload": True,
                            "with_vectors": False
                        },
                        timeout=10.0
                    )
                    if response.status_code == 200:
                        data = response.json()
                        search_data = data.get("result", [])
                        # Convert dict results to object-like structure
                        from types import SimpleNamespace
                        full_query_results = []
                        for r in search_data:
                            if isinstance(r, dict):
                                full_query_results.append(SimpleNamespace(
                                    payload=r.get("payload", {}),
                                    score=r.get("score", 0.0),
                                    id=r.get("id", 0)
                                ))
                            else:
                                # Already an object
                                full_query_results.append(r)
                
                logger.info(f"Full query search returned {len(full_query_results)} results")
                
                for result in full_query_results:
                    # Handle both dict and object results
                    if isinstance(result, dict):
                        payload = result.get("payload", {})
                        score = result.get("score", 0.0)
                    else:
                        payload = result.payload if hasattr(result, 'payload') else {}
                        score = result.score if hasattr(result, 'score') else 0.0
                    
                    cui = payload.get("cui", "") if isinstance(payload, dict) else getattr(payload, 'cui', '')
                    if cui:
                        all_cuis.add(cui)
                        preferred_name = payload.get("preferred_name", "") if isinstance(payload, dict) else getattr(payload, 'preferred_name', '')
                        semantic_types = payload.get("semantic_types", []) if isinstance(payload, dict) else getattr(payload, 'semantic_types', [])
                        text = payload.get("text", "") if isinstance(payload, dict) else getattr(payload, 'text', '')
                        
                        concept_details[cui] = {
                            "preferred_name": preferred_name,
                            "semantic_types": semantic_types,
                            "text": text
                        }
                        
                        all_results.append({
                            "text": text or f"Concept: {preferred_name}",
                            "source": f"CUI:{cui}",
                            "score": str(float(score)),
                            "entity_matched": "full_query"
                        })
                        logger.debug(f"Added full query result: CUI {cui} ({preferred_name[:50] if preferred_name else 'N/A'})")
            except Exception as e:
                logger.error(f"Error in full query search: {e}", exc_info=True)
            
            # Also try entity-specific searches
            for entity in entities:
                try:
                    logger.debug(f"Searching Qdrant for entity: '{entity}'")
                    # Generate embedding for each entity
                    entity_embedding = self.embedding_model.encode([entity], show_progress_bar=False)[0]
                    
                    # Search Qdrant with fallback
                    search_results = []
                    if hasattr(client, 'search'):
                        try:
                            search_results = client.search(
                                collection_name=self.collection_name,
                                query_vector=entity_embedding.tolist(),
                                limit=top_k,
                                with_payload=True,
                                with_vectors=False
                            )
                        except AttributeError:
                            search_results = []
                    
                    # Fallback to HTTP if search doesn't work
                    if not search_results:
                        import httpx
                        response = httpx.post(
                            f"{settings.QDRANT_URL}/collections/{self.collection_name}/points/search",
                            json={
                                "vector": entity_embedding.tolist(),
                                "limit": top_k,
                                "with_payload": True,
                                "with_vectors": False
                            },
                            timeout=10.0
                        )
                        if response.status_code == 200:
                            data = response.json()
                            search_data = data.get("result", [])
                            # Convert dict results to object-like structure
                            from types import SimpleNamespace
                            search_results = []
                            for r in search_data:
                                if isinstance(r, dict):
                                    search_results.append(SimpleNamespace(
                                        payload=r.get("payload", {}),
                                        score=r.get("score", 0.0),
                                        id=r.get("id", 0)
                                    ))
                                else:
                                    search_results.append(r)
                    
                    logger.info(f"Entity '{entity}' search returned {len(search_results)} results")
                    
                    for result in search_results:
                        # Handle both dict and object results
                        if isinstance(result, dict):
                            payload = result.get("payload", {})
                            score = result.get("score", 0.0)
                        else:
                            payload = result.payload if hasattr(result, 'payload') else {}
                            score = result.score if hasattr(result, 'score') else 0.0
                        
                        cui = payload.get("cui", "") if isinstance(payload, dict) else getattr(payload, 'cui', '')
                        if cui:
                            all_cuis.add(cui)
                            preferred_name = payload.get("preferred_name", "") if isinstance(payload, dict) else getattr(payload, 'preferred_name', '')
                            semantic_types = payload.get("semantic_types", []) if isinstance(payload, dict) else getattr(payload, 'semantic_types', [])
                            text = payload.get("text", "") if isinstance(payload, dict) else getattr(payload, 'text', '')
                            
                            concept_details[cui] = {
                                "preferred_name": preferred_name,
                                "semantic_types": semantic_types,
                                "text": text
                            }
                            
                            # Only add if not already in results (deduplicate)
                            if cui not in [r["source"].replace("CUI:", "") for r in all_results]:
                                all_results.append({
                                    "text": text or f"Concept: {preferred_name}",
                                    "source": f"CUI:{cui}",
                                    "score": str(float(score)),
                                    "entity_matched": entity
                                })
                                logger.debug(f"Added entity result: CUI {cui} ({preferred_name[:50] if preferred_name else 'N/A'})")
                except Exception as e:
                    logger.warning(f"Error searching for entity '{entity}': {e}")
                    continue
            
            # Deduplicate results by CUI, keeping highest score
            results_dict = {}
            for result in all_results:
                cui = result["source"].replace("CUI:", "")
                if cui and (cui not in results_dict or float(result["score"]) > float(results_dict[cui]["score"])):
                    results_dict[cui] = result
            
            results = list(results_dict.values())[:top_k * 2]  # Get more for relationship expansion
            umls_concepts = list(all_cuis)
            
            logger.info(f"After similarity search: {len(results)} results, {len(umls_concepts)} unique CUIs")
            
            # If no results from similarity search, log warning but continue (might get relations)
            if not results:
                logger.warning(f"No results found from similarity search for query: {query}")
                logger.warning("This might indicate: 1) Query doesn't match indexed text, 2) Embedding mismatch, or 3) Collection issue")
                # Don't return early - might still get relations from CUIs if we have any
                if not umls_concepts:
                    return [], []
            
            # STEP 3: Relationship retrieval (optional - don't fail if this errors)
            relations = []
            try:
                logger.info(f"Retrieving relations for {len(umls_concepts)} concepts...")
                relations = await self._get_relations(umls_concepts)
                logger.info(f"Found {len(relations)} relations")
                
                # Add relations as context
                for rel in relations[:20]:  # Limit relations
                    results.append({
                        "text": rel["text"],
                        "source": f"RELATION:{rel['relation_type']}",
                        "score": "0.9",  # High score for explicit relations
                        "relation_type": rel["relation_type"]
                    })
            except Exception as rel_err:
                logger.warning(f"Error retrieving relations (continuing anyway): {rel_err}")
            
            # STEP 4: Drug-drug interaction mapping (optional - don't fail if this errors)
            drug_cuis = []
            try:
                # Identify drugs
                drug_cuis = [
                    cui for cui in umls_concepts
                    if cui in concept_details and self._is_drug(concept_details[cui].get("semantic_types", []))
                ]
                
                if len(drug_cuis) >= 2:
                    logger.info(f"Found {len(drug_cuis)} drug concepts, mapping interactions...")
                    interactions = await self._get_drug_interactions(drug_cuis)
                    logger.info(f"Found {len(interactions)} drug interactions")
                    
                    # Add interactions as high-priority context
                    for interaction in interactions[:10]:  # Limit interactions
                        results.insert(0, {  # Insert at beginning (high priority)
                            "text": f"DRUG INTERACTION: {interaction['text']}",
                            "source": f"INTERACTION:{interaction['relation_type']}",
                            "score": "0.95",  # Very high score for drug interactions
                            "interaction_type": "drug_drug"
                        })
            except Exception as drug_err:
                logger.warning(f"Error retrieving drug interactions (continuing anyway): {drug_err}")
            
            # Sort by score and limit
            results = sorted(results, key=lambda x: float(x["score"]), reverse=True)[:top_k * 3]
            
            logger.info(f"Retrieved {len(results)} total results ({len(umls_concepts)} concepts, {len(relations)} relations, {len(drug_cuis)} drugs)")
            
            # Ensure we return at least the basic similarity results even if relations/interactions failed
            if not results and all_results:
                logger.warning("No results after processing, but had basic search results. Returning basic results.")
                results = all_results[:top_k]
            
            return results, list(set(umls_concepts))
            
        except Exception as e:
            logger.error(f"Error retrieving from Qdrant: {e}", exc_info=True)
            logger.error(f"Query was: {query}")
            return [], []
    
    async def index_documents(self, documents: List[Dict[str, str]]):
        """
        Index documents in Qdrant
        Note: This is a placeholder - documents should be indexed via generate_embeddings_qdrant.py
        """
        logger.warning("index_documents called on Qdrant RAG service. Use generate_embeddings_qdrant.py to index documents.")
        pass

