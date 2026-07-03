"""
Quick query tool for UMLS database
Run queries without needing pgAdmin
"""
import sys
from sqlalchemy import create_engine, text
from api.config import settings

def run_query(query: str, limit: int = 100):
    """Run a SQL query and display results"""
    engine = create_engine(settings.DATABASE_URL)
    
    try:
        with engine.connect() as conn:
            # Add LIMIT if not present and it's a SELECT query
            if query.strip().upper().startswith('SELECT') and 'LIMIT' not in query.upper():
                query = f"{query.rstrip(';')} LIMIT {limit}"
            
            result = conn.execute(text(query))
            
            # Get column names
            columns = result.keys()
            
            # Fetch results
            rows = result.fetchall()
            
            if not rows:
                print("Query executed successfully. No results returned.")
                return
            
            # Display results
            print(f"\n{'='*80}")
            print(f"Query Results ({len(rows)} rows)")
            print(f"{'='*80}\n")
            
            # Simple table display
            print(" | ".join(str(col) for col in columns))
            print("-" * 80)
            for row in rows:
                print(" | ".join(str(val)[:50] for val in row))  # Truncate long values
            
            print(f"\n{'='*80}")
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def interactive_mode():
    """Interactive query mode"""
    engine = create_engine(settings.DATABASE_URL)
    
    print("="*80)
    print("UMLS Database Query Tool")
    print("="*80)
    print("Type SQL queries (or 'exit' to quit, 'help' for examples)")
    print("="*80)
    
    while True:
        try:
            query = input("\nSQL> ").strip()
            
            if not query:
                continue
            
            if query.lower() == 'exit':
                break
            
            if query.lower() == 'help':
                print("""
Example queries:
  SELECT COUNT(*) FROM umls_concepts;
  SELECT * FROM umls_concepts LIMIT 10;
  SELECT COUNT(*) FROM umls_relations;
  SELECT relation_type, COUNT(*) FROM umls_relations GROUP BY relation_type LIMIT 10;
                """)
                continue
            
            run_query(query)
            
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Run query from command line
        query = " ".join(sys.argv[1:])
        run_query(query)
    else:
        # Interactive mode
        interactive_mode()

