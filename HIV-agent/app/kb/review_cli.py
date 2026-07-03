import json
import os
from pathlib import Path
import argparse
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

def render_page_image(pdf_path: str, page_num: int, output_img: str):
    """Render a PDF page as an image using PyMuPDF."""
    if not fitz:
        print("PyMuPDF not installed. Skipping image generation.")
        return
    try:
        doc = fitz.open(pdf_path)
        page = doc.load_page(page_num - 1)  # 0-indexed
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        pix.save(output_img)
        print(f"Generated page screenshot: {output_img}")
    except Exception as e:
        print(f"Error generating screenshot: {e}")

def main():
    parser = argparse.ArgumentParser(description="Manual review tool for extracted KB tables.")
    parser.add_argument("--disease", required=True, help="Disease folder to review")
    args = parser.parse_args()

    flagged_dir = Path("app/kb/flagged") / args.disease
    validated_dir = Path("app/kb/validated") / args.disease
    docs_dir = Path("app/docs")
    
    if not flagged_dir.exists():
        print(f"No flagged tables for {args.disease}.")
        return

    validated_dir.mkdir(parents=True, exist_ok=True)

    flagged_files = list(flagged_dir.glob("*.json"))
    if not flagged_files:
        print("No flagged files to review.")
        return

    print(f"Found {len(flagged_files)} flagged files for review.")
    
    for f_path in flagged_files:
        with open(f_path, "r") as f:
            table_data = json.load(f)
            
        print("\n" + "="*50)
        print(f"Reviewing: {f_path.name}")
        print(f"Error: {table_data.get('validation_error', 'Unknown')}")
        print(f"Type: {table_data.get('type')}")
        
        # Generate screenshot if possible
        src_file = table_data.get("source", {}).get("file")
        page_num = table_data.get("source", {}).get("page")
        if src_file and page_num:
            pdf_path = docs_dir / src_file
            if pdf_path.exists():
                img_out = f_path.with_suffix('.png')
                render_page_image(str(pdf_path), page_num, str(img_out))
                
        print("\nCurrent Data (First 2 rows):")
        for row in table_data.get("data", [])[:2]:
            print(row)
            
        # Minimal interactive review
        print("\nOptions:")
        print("1. Accept as is (move to validated)")
        print("2. Reject (leave in flagged, edit JSON manually later)")
        print("3. Skip for now")
        
        choice = input("Choice [1/2/3]: ").strip()
        
        if choice == "1":
            if "validation_error" in table_data:
                del table_data["validation_error"]
            out_file = validated_dir / f_path.name
            with open(out_file, "w") as f:
                json.dump(table_data, f, indent=2)
            f_path.unlink()  # Remove from flagged
            print("Moved to validated.")
        elif choice == "2":
            print("Rejected. Edit JSON manually.")
        else:
            print("Skipped.")

if __name__ == "__main__":
    main()
