"""
Load transformed UMLS data into PostgreSQL
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from api.db import Base, UMLSConcept, UMLSRelation
from api.config import settings
from etl.transform_umls import transform_mrconso, transform_mrsty, transform_mrrel, parse_rrf_file
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def load_concepts(concepts: list, batch_size: int = 1000):
    """Load concepts into database"""
    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Group concepts by CUI
        concept_dict = {}
        for concept in concepts:
            cui = concept["cui"]
            if cui not in concept_dict:
                concept_dict[cui] = {
                    "cui": cui,
                    "preferred_name": "",
                    "synonyms": [],
                    "semantic_types": []
                }
            
            if concept["preferred"]:
                concept_dict[cui]["preferred_name"] = concept["string"]
            else:
                concept_dict[cui]["synonyms"].append(concept["string"])
        
        # Insert in batches
        for i, (cui, data) in enumerate(concept_dict.items()):
            umls_concept = UMLSConcept(
                cui=cui,
                preferred_name=data["preferred_name"] or data["synonyms"][0] if data["synonyms"] else "",
                synonyms=data["synonyms"],
                semantic_types=[]  # Will be populated from MRSTY
            )
            session.add(umls_concept)
            
            if (i + 1) % batch_size == 0:
                session.commit()
                logger.info(f"Loaded {i + 1} concepts")
        
        session.commit()
        logger.info(f"Loaded {len(concept_dict)} concepts")
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error loading concepts: {e}")
        raise
    finally:
        session.close()


def load_relations(relations: list, batch_size: int = 1000):
    """Load relations into database"""
    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        for i, relation in enumerate(relations):
            umls_relation = UMLSRelation(
                cui1=relation["cui1"],
                cui2=relation["cui2"],
                relation_type=relation["relation"],
                relation_label=relation["relation_label"]
            )
            session.add(umls_relation)
            
            if (i + 1) % batch_size == 0:
                session.commit()
                logger.info(f"Loaded {i + 1} relations")
        
        session.commit()
        logger.info(f"Loaded {len(relations)} relations")
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error loading relations: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    # Create tables
    engine = create_engine(settings.DATABASE_URL)
    Base.metadata.create_all(engine)
    
    # Load data
    data_dir = Path("data/umls")
    
    # Load concepts
    if (data_dir / "MRCONSO.RRF").exists():
        records = parse_rrf_file(str(data_dir / "MRCONSO.RRF"))
        concepts = transform_mrconso(records)
        load_concepts(concepts)
    
    # Load relations
    if (data_dir / "MRREL.RRF").exists():
        records = parse_rrf_file(str(data_dir / "MRREL.RRF"))
        relations = transform_mrrel(records)
        load_relations(relations)

