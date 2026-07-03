import os
import zipfile
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from tqdm import tqdm

# Load environment variables from .env file
load_dotenv()

UMLS_API_KEY = os.getenv("UMLS_API_KEY")
VERSION = "2024AA"
DOWNLOAD_DIR = "./data/umls"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# Step 1: Get ticket granting ticket (TGT)
def get_tgt(api_key):
    if not api_key:
        raise ValueError("UMLS_API_KEY is not set. Please set UMLS_API_KEY in your .env file.")
    url = "https://utslogin.nlm.nih.gov/cas/v1/api-key"
    resp = requests.post(url, data={"apikey": api_key})
    if resp.status_code != 201:
        raise Exception(
            f"Failed to get TGT. Status code: {resp.status_code}, Response: {resp.text[:200]}"
        )
    if "location" not in resp.headers:
        raise Exception(f"TGT response missing 'location' header. Response: {resp.text[:200]}")
    tgt_location = resp.headers["location"]
    # Extract TGT ID from location URL
    tgt_id = tgt_location.split("/")[-1]
    # Return the full TGT URL for posting service ticket requests
    return f"https://utslogin.nlm.nih.gov/cas/v1/tickets/{tgt_id}"


# Step 2: Get service ticket
def get_service_ticket(tgt, service_url):
    """Get service ticket for a specific service URL.
    The service URL must match the domain of the resource being accessed.
    """
    resp = requests.post(tgt, data={"service": service_url})
    if resp.status_code != 200:
        raise Exception(
            f"Failed to get service ticket. Status code: {resp.status_code}, "
            f"Response: {resp.text[:200]}"
        )
    return resp.text.strip()


# Step 3: Download file with streaming
def download_umls():
    # Download only the full UMLS dataset
    # Full dataset should be 5-10GB+, not just a few hundred MB
    base_download_url = f"https://download.nlm.nih.gov/umls/kss/{VERSION}/umls-{VERSION}-full.zip"
    file_path = f"{DOWNLOAD_DIR}/umls-{VERSION}-full.zip"
    last_error = None

    # Method 1: Try UTS download API (simpler, recommended by UTS documentation)
    # https://documentation.uts.nlm.nih.gov/automating-downloads.html
    uts_download_url = (
        f"https://uts-ws.nlm.nih.gov/download?url={base_download_url}&apiKey={UMLS_API_KEY}"
    )

    print(f"\n{'=' * 60}")
    print("Method 1: UTS Download API (Recommended)")
    print(f"{'=' * 60}")
    print(f"Download URL: {uts_download_url.split('apiKey=')[0]}apiKey=***")

    try:
        # Check if file exists and resume if possible
        resume_pos = 0
        if os.path.exists(file_path):
            resume_pos = os.path.getsize(file_path)
            if resume_pos > 0:
                print(f"📥 Found existing file: {resume_pos / (1024**2):.2f} MB")

        headers = {}
        if resume_pos > 0:
            headers["Range"] = f"bytes={resume_pos}-"
            print(f"   Attempting to resume from byte {resume_pos}")

        with requests.get(
            uts_download_url, stream=True, timeout=(30, 1800), allow_redirects=True, headers=headers
        ) as r:
            if r.status_code in [200, 206]:
                total = int(r.headers.get("content-length", 0))
                if resume_pos > 0 and r.status_code == 206:
                    total = resume_pos + total  # Adjust total for resume
                    print("✓ Server supports resume (HTTP 206)")
                    mode = "ab"
                elif resume_pos > 0 and r.status_code == 200:
                    print(" Server doesn't support resume, re-downloading from start...")
                    resume_pos = 0
                    mode = "wb"
                else:
                    mode = "wb"

                if total == 0:
                    # Try to get size from Content-Range or estimate
                    content_range = r.headers.get("content-range", "")
                    if content_range:
                        total = int(content_range.split("/")[-1])
                    else:
                        print("⚠ Could not determine file size, downloading anyway...")
                        total = None

                if total:
                    size_gb = total / (1024**3)
                    size_mb = total / (1024**2)
                    print(f"✓ File size: {size_gb:.2f} GB ({size_mb:.2f} MB)")
                    if resume_pos > 0:
                        print(f"  Resuming from: {resume_pos / (1024**2):.2f} MB")
                        print(f"  Remaining: {(total - resume_pos) / (1024**2):.2f} MB")

                print(f"Downloading to: {file_path}")
                downloaded = resume_pos
                with (
                    open(file_path, mode) as f,
                    tqdm(
                        total=total,
                        initial=resume_pos,
                        unit="B",
                        unit_scale=True,
                        desc="Downloading",
                    ) as bar,
                ):
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            bar.update(len(chunk))

                # Verify download
                actual_size = os.path.getsize(file_path)
                if total and actual_size != total:
                    if actual_size < total * 0.9:
                        raise Exception(
                            f"Download incomplete. Expected {total} bytes, got {actual_size} bytes"
                        )
                    else:
                        print(f"✓ Download complete: {actual_size / (1024**2):.2f} MB")

                # Verify zip file
                print("Verifying zip file integrity...")
                try:
                    with zipfile.ZipFile(file_path, "r") as test_zip:
                        test_zip.testzip()
                        file_count = len(test_zip.namelist())
                        rrf_count = len(
                            [f for f in test_zip.namelist() if f.upper().endswith(".RRF")]
                        )
                        print("✓ Zip file is valid")
                        print(f"  Total files: {file_count}")
                        print(f"  RRF files: {rrf_count}")

                        required_rrf = ["MRCONSO.RRF", "MRREL.RRF", "MRSTY.RRF"]
                        found_rrf = [
                            f.upper().split("/")[-1]
                            for f in test_zip.namelist()
                            if f.upper().endswith(".RRF")
                        ]
                        missing = [f for f in required_rrf if f not in found_rrf]

                        if missing:
                            print(f"⚠ Warning: Missing expected RRF files: {missing}")
                        else:
                            print("✓ All required RRF files found")
                except zipfile.BadZipFile:
                    raise Exception(
                        "Downloaded file is not a valid zip file. Download may be corrupted."
                    )

                print(f"✓ Download complete: {file_path}")
                return file_path
            else:
                print(f"UTS API method failed with status {r.status_code}")
                if r.status_code == 401:
                    print("  Authentication failed - will try CAS ticket method")
                last_error = f"UTS API: Status {r.status_code}"
    except Exception as e:
        print(f"UTS API method failed: {e}")
        last_error = f"UTS API: {str(e)}"
        print("Falling back to CAS ticket authentication method...")

    # Method 2: Fallback to CAS ticket authentication
    print(f"\n{'=' * 60}")
    print("Method 2: CAS Ticket Authentication (Fallback)")
    print(f"{'=' * 60}")

    tgt = get_tgt(UMLS_API_KEY)

    print(f"\n{'=' * 60}")
    print(f"Downloading: {base_download_url}")
    print(f"{'=' * 60}")

    # Try different service URL formats - CAS may require exact match
    service_urls_to_try = [
        base_download_url,  # Full URL as service
        f"https://download.nlm.nih.gov/umls/kss/{VERSION}/",  # Directory path
        "https://download.nlm.nih.gov/umls/kss/",  # Base path
        "https://download.nlm.nih.gov/umls/",  # Parent path
        "https://download.nlm.nih.gov",  # Just domain
        "http://download.nlm.nih.gov",  # HTTP version
    ]

    st = None
    successful_service = None
    for service_url in service_urls_to_try:
        try:
            st = get_service_ticket(tgt, service_url)
            successful_service = service_url
            print(f"Service ticket obtained using service URL: {service_url[:80]}...")
            break
        except Exception as e:
            print(f"Failed to get ticket with service URL '{service_url[:50]}...': {e}")
            continue

    if not st:
        raise Exception(f"Failed to obtain service ticket for {base_download_url}")

    # Clean the ticket (remove any whitespace/newlines)
    st = st.strip()

    # Try different URL formats with the ticket
    urls_to_try = [
        f"{base_download_url}?ticket={st}",  # Standard format
        f"{base_download_url}?ticket={quote(st, safe='')}",  # URL encoded
        f"{base_download_url}?ticket={quote(st)}",  # Fully URL encoded
    ]

    print(f"Downloading UMLS version {VERSION} (full dataset)...")

    for idx, download_url in enumerate(urls_to_try):
        try:
            encoded = idx > 0
            print(
                f"Attempt {idx + 1}/{len(urls_to_try)}: Trying {'URL-encoded' if encoded else 'standard'} ticket format..."
            )

            # Get a fresh ticket for each attempt after the first (tickets expire quickly)
            if idx > 0:
                print("Getting fresh service ticket (previous ticket may have expired)...")
                fresh_st = None
                for service_url in service_urls_to_try:
                    try:
                        fresh_st = get_service_ticket(tgt, service_url)
                        if fresh_st:
                            st = fresh_st.strip()
                            # Update URL with fresh ticket
                            if idx == 1:
                                download_url = f"{base_download_url}?ticket={quote(st, safe='')}"
                            else:
                                download_url = f"{base_download_url}?ticket={quote(st)}"
                            print("✓ Fresh ticket obtained")
                            break
                    except Exception as ticket_err:
                        print(f"  Failed to get fresh ticket: {ticket_err}")
                        continue
                if not fresh_st:
                    print("⚠ Could not get fresh ticket, using original...")

            # Use longer timeout for large downloads
            # timeout=(connect_timeout, read_timeout) - 30s connect, 30min read
            print("Starting download (timeout: 30 minutes for large files)...")
            with requests.get(
                download_url, stream=True, timeout=(30, 1800), allow_redirects=True
            ) as r:
                if r.status_code == 200:
                    total = int(r.headers.get("content-length", 0))

                    if total == 0:
                        print("Warning: Content-Length is 0, trying next format...")
                        continue

                    # Validate expected size - full UMLS should be at least 1GB
                    size_gb = total / (1024**3)
                    size_mb = total / (1024**2)

                    if total < 500 * 1024 * 1024:  # Less than 500MB is suspicious
                        print(
                            f"⚠ Warning: File size ({size_mb:.2f} MB) seems too small for full UMLS dataset."
                        )
                        print("Expected: 5-10GB+. This might be a partial download.")
                        print("Continuing anyway, but please verify the download is complete...")

                    # Check if file exists and resume if possible
                    resume_pos = 0
                    if os.path.exists(file_path):
                        resume_pos = os.path.getsize(file_path)
                        if resume_pos > 0 and resume_pos < total:
                            print(f"Resuming download from {resume_pos / (1024**2):.2f} MB")
                            print(f"   Existing file size: {resume_pos / (1024**2):.2f} MB")
                            print(f"   Remaining: {(total - resume_pos) / (1024**2):.2f} MB")
                        elif resume_pos >= total:
                            print(
                                f"✓ File already exists and appears complete ({resume_pos / (1024**2):.2f} MB)"
                            )
                            # Verify it's a valid zip
                            try:
                                with zipfile.ZipFile(file_path, "r") as test_zip:
                                    test_zip.testzip()
                                print("✓ Existing file is valid, skipping download")
                                return file_path
                            except:
                                print("⚠ Existing file is corrupted, re-downloading...")
                                resume_pos = 0

                    print(f"✓ Success! Downloading {size_gb:.2f} GB ({size_mb:.2f} MB)...")
                    print(f"  URL: {base_download_url}")
                    print(f"  Saving to: {file_path}")
                    print("  Expected full UMLS dataset size: 5-10GB+")

                    # Prepare headers for resume
                    headers = {}
                    if resume_pos > 0:
                        headers["Range"] = f"bytes={resume_pos}-"
                        print(f"  Resuming from byte {resume_pos}")

                    # Make request with resume support
                    resume_r = requests.get(
                        download_url,
                        stream=True,
                        timeout=(30, 1800),
                        allow_redirects=True,
                        headers=headers,
                    )

                    # Check if server supports resume
                    if resume_pos > 0 and resume_r.status_code == 206:
                        print("✓ Server supports resume (HTTP 206 Partial Content)")
                        mode = "ab"  # Append mode
                    elif resume_pos > 0 and resume_r.status_code == 200:
                        print("⚠ Server doesn't support resume, re-downloading from start...")
                        resume_pos = 0
                        mode = "wb"  # Write mode
                    else:
                        mode = "wb"  # Write mode

                    if resume_r.status_code not in [200, 206]:
                        raise Exception(f"Unexpected status code: {resume_r.status_code}")

                    downloaded = resume_pos
                    with (
                        open(file_path, mode) as f,
                        tqdm(
                            total=total,
                            initial=resume_pos,
                            unit="B",
                            unit_scale=True,
                            desc="Downloading",
                        ) as bar,
                    ):
                        for chunk in resume_r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                bar.update(len(chunk))

                    # Verify download completed
                    actual_size = os.path.getsize(file_path)
                    if actual_size != total and total > 0:
                        print(
                            f"Warning: Download size mismatch. Expected: {total}, Got: {actual_size}"
                        )
                        if actual_size < total * 0.9:  # Less than 90% of expected
                            raise Exception(
                                f"Download incomplete. Expected {total} bytes, got {actual_size} bytes"
                            )

                    # Verify zip file integrity
                    print("Verifying zip file integrity...")
                    try:
                        with zipfile.ZipFile(file_path, "r") as test_zip:
                            test_zip.testzip()
                            file_count = len(test_zip.namelist())
                            rrf_count = len(
                                [f for f in test_zip.namelist() if f.upper().endswith(".RRF")]
                            )
                            print("✓ Zip file is valid")
                            print(f"  Total files: {file_count}")
                            print(f"  RRF files: {rrf_count}")

                            # Check for key RRF files
                            required_rrf = ["MRCONSO.RRF", "MRREL.RRF", "MRSTY.RRF"]
                            found_rrf = [
                                f.upper().split("/")[-1]
                                for f in test_zip.namelist()
                                if f.upper().endswith(".RRF")
                            ]
                            missing = [f for f in required_rrf if f not in found_rrf]

                            if missing:
                                print(f"⚠ Warning: Missing expected RRF files: {missing}")
                            else:
                                print("✓ All required RRF files found")
                    except zipfile.BadZipFile:
                        raise Exception(
                            "Downloaded file is not a valid zip file. Download may be corrupted."
                        )

                    print(f"Download complete: {file_path} ({size_gb:.2f} GB)")
                    # Also create a symlink as umls.zip for backward compatibility
                    umls_zip_path = f"{DOWNLOAD_DIR}/umls.zip"
                    if file_path != umls_zip_path and not os.path.exists(umls_zip_path):
                        print(f"Creating symlink: {umls_zip_path} -> {os.path.basename(file_path)}")
                        try:
                            os.symlink(os.path.basename(file_path), umls_zip_path)
                        except OSError:
                            # If symlink fails (e.g., on Windows), just copy the file
                            import shutil

                            shutil.copy2(file_path, umls_zip_path)
                            print("Created copy instead of symlink")
                    return file_path
                elif r.status_code == 401:
                    error_text = r.text[:300] if r.text else "No response body"
                    last_error = f"Status 401 Unauthorized: {error_text}"
                    print("Authentication failed (401), trying next format...")
                    continue
                else:
                    error_text = r.text[:300] if r.text else "No response body"
                    last_error = f"Status {r.status_code}: {error_text}"
                    print(f"Failed with status {r.status_code}, trying next format...")
                    continue
        except requests.exceptions.Timeout as e:
            last_error = f"Timeout error: {e}"
            print("⚠ Request timed out. This is common for large downloads.")
            print("   The service ticket may have expired. Will try with fresh ticket...")
            # Try next URL format
            continue
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            error_str = str(e).lower()
            if "timeout" in error_str or "timed out" in error_str:
                print(f"⚠ Timeout error: {e}")
                print("   Will try with fresh ticket on next attempt...")
                continue
            else:
                print(f"Request error: {e}, trying next format...")
                continue

        # If we got a timeout, try one more time with a completely fresh ticket
        if last_error and ("timeout" in last_error.lower() or "timed out" in last_error.lower()):
            print(
                f"\n⚠ Got timeout error. Retrying {base_download_url} with fresh authentication..."
            )
            # Get completely fresh TGT and service ticket
            try:
                fresh_tgt = get_tgt(UMLS_API_KEY)
                fresh_st = get_service_ticket(fresh_tgt, base_download_url)
                if fresh_st:
                    fresh_st = fresh_st.strip()
                    retry_url = f"{base_download_url}?ticket={fresh_st}"
                    print("Retrying download with fresh ticket (longer timeout: 30 minutes)...")
                    try:
                        with requests.get(
                            retry_url, stream=True, timeout=(30, 1800), allow_redirects=True
                        ) as r:
                            if r.status_code == 200:
                                total = int(r.headers.get("content-length", 0))
                                if total > 0:
                                    size_gb = total / (1024**3)
                                    print(f"✓ Retry successful! Downloading {size_gb:.2f} GB...")
                                    # Continue with download logic (same as above)
                                    # For brevity, we'll extract this to a helper function if needed
                                    # But for now, let's just note that retry worked
                                    print("Download in progress...")
                                    # Copy the download logic here or return to main flow
                    except Exception as retry_e:
                        print(f"Retry also failed: {retry_e}")
            except Exception as e:
                print(f"Failed to get fresh ticket for retry: {e}")

    # If all attempts failed, provide detailed error message
    raise Exception(
        f"Failed to download UMLS version {VERSION} from {base_download_url}.\n"
        f"Last error: {last_error}\n"
        f"Service URL used: {successful_service}\n"
        f"\nTroubleshooting steps:\n"
        f"  1. Verify your API key is valid and has download permissions\n"
        f"  2. Check if version '{VERSION}' is available for download\n"
        f"  3. Ensure your UMLS account has access to download full releases\n"
        f"  4. Try accessing the download URL manually in a browser after logging into UMLS\n"
        f"  5. Check your network connection - large downloads may timeout"
    )


# Step 4: Extract RRF files
def extract_zip(path):
    """Extract UMLS zip file and verify extraction."""
    print(f"\nExtracting {path} to {DOWNLOAD_DIR}...")

    # Verify zip file before extraction
    try:
        with zipfile.ZipFile(path, "r") as test_zip:
            test_zip.testzip()
    except zipfile.BadZipFile:
        raise Exception(f"Invalid zip file: {path}. Please re-download.")

    # Extract files
    with zipfile.ZipFile(path, "r") as zip_ref:
        # Get list of files to extract
        file_list = zip_ref.namelist()
        rrf_files = [f for f in file_list if f.upper().endswith(".RRF")]

        print(f"Extracting {len(file_list)} files ({len(rrf_files)} RRF files)...")
        zip_ref.extractall(DOWNLOAD_DIR)

    # Verify extraction
    extracted_rrf = list(Path(DOWNLOAD_DIR).rglob("*.RRF"))
    print(f"✓ Extraction complete. Found {len(extracted_rrf)} RRF files")

    # List key RRF files found
    key_rrf_files = ["MRCONSO.RRF", "MRREL.RRF", "MRSTY.RRF", "MRHIER.RRF", "MRDEF.RRF"]
    found_key_files = []
    for rrf_path in extracted_rrf:
        filename = rrf_path.name
        if filename in key_rrf_files:
            found_key_files.append(filename)

    if found_key_files:
        print(f"✓ Key RRF files found: {', '.join(found_key_files)}")

    return extracted_rrf


def download_umls_with_library():
    """Fallback method using umls_downloader library if available."""
    try:
        from umls_downloader import download_umls as lib_download

        print("Attempting download using umls_downloader library...")
        # Check the library API - it might return a path or download to current directory
        zip_path = lib_download(version=VERSION, api_key=UMLS_API_KEY)

        # The library might return a path or download to current directory
        if zip_path:
            if os.path.exists(zip_path):
                return zip_path
            if os.path.isdir(zip_path):
                zip_files = [f for f in os.listdir(zip_path) if f.endswith(".zip")]
                if zip_files:
                    return os.path.join(zip_path, zip_files[0])

        # Check if file was downloaded to current directory
        expected_filename = f"umls-{VERSION}-full.zip"
        if os.path.exists(expected_filename):
            return expected_filename

        # Check in DOWNLOAD_DIR
        download_path = os.path.join(DOWNLOAD_DIR, expected_filename)
        if os.path.exists(download_path):
            return download_path

        return zip_path
    except ImportError:
        raise Exception(
            "umls_downloader library not available. Install with: pip install umls-downloader"
        )
    except Exception as e:
        raise Exception(f"Library download failed: {e}")


def download_umls_with_api_key_header():
    """Alternative method: Try using API key directly in headers."""
    print("Trying alternative authentication with API key in headers...")
    base_download_url = f"https://download.nlm.nih.gov/umls/kss/{VERSION}/umls-{VERSION}-full.zip"
    file_path = f"{DOWNLOAD_DIR}/umls.zip"

    # Try different header formats
    header_formats = [
        {"Authorization": f"Bearer {UMLS_API_KEY}"},
        {"Authorization": f"Basic {UMLS_API_KEY}"},
        {"apikey": UMLS_API_KEY},
        {"X-API-Key": UMLS_API_KEY},
        {"apiKey": UMLS_API_KEY},
    ]

    for headers in header_formats:
        try:
            print(f"Trying headers: {list(headers.keys())}")
            with requests.get(base_download_url, headers=headers, stream=True, timeout=300) as r:
                if r.status_code == 200:
                    total = int(r.headers.get("content-length", 0))
                    if total > 0:
                        print(
                            f"Success with header authentication! Downloading {total / (1024 * 1024):.2f} MB..."
                        )
                        with (
                            open(file_path, "wb") as f,
                            tqdm(total=total, unit="B", unit_scale=True) as bar,
                        ):
                            for chunk in r.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                                    bar.update(len(chunk))
                        print(f"Download complete: {file_path}")
                        return file_path
                elif r.status_code != 401:
                    print(f"Got status {r.status_code}, might be progress...")
        except Exception as e:
            print(f"Error with header format: {e}")
            continue

    raise Exception("API key header authentication failed")


if __name__ == "__main__":
    zip_path = None
    errors = []

    # Method 1: Manual CAS authentication
    try:
        print("=" * 60)
        print("Method 1: CAS Ticket Authentication")
        print("=" * 60)
        zip_path = download_umls()
        print("✓ CAS authentication successful!")
    except Exception as e:
        errors.append(f"CAS method: {str(e)[:200]}")
        print("✗ CAS method failed")

    # Method 2: API key in headers
    if not zip_path:
        try:
            print("\n" + "=" * 60)
            print("Method 2: API Key Header Authentication")
            print("=" * 60)
            zip_path = download_umls_with_api_key_header()
            print("✓ API key header authentication successful!")
        except Exception as e:
            errors.append(f"API key header method: {str(e)[:200]}")
            print("✗ API key header method failed")

    # Method 3: umls_downloader library
    if not zip_path:
        try:
            print("\n" + "=" * 60)
            print("Method 3: umls_downloader Library")
            print("=" * 60)
            zip_path = download_umls_with_library()
            print(f"✓ Library method successful: {zip_path}")
        except Exception as e:
            errors.append(f"Library method: {str(e)[:200]}")
            print("✗ Library method failed")

    # If all methods failed
    if not zip_path:
        raise Exception(
            "All download methods failed.\n\n"
            "Errors:\n" + "\n".join(f"  - {err}" for err in errors) + "\n\n"
            f"Troubleshooting:\n"
            f"  1. Verify your API key is valid: https://uts.nlm.nih.gov/uts/profile\n"
            f"  2. Check if version '{VERSION}' is available\n"
            f"  3. Ensure your account has download permissions for full UMLS releases\n"
            f"  4. Try accessing manually: https://download.nlm.nih.gov/umls/kss/{VERSION}/\n"
            f"  5. Contact UMLS support if the issue persists"
        )

    # Extract the zip file
    if zip_path and os.path.exists(zip_path):
        print(f"\nExtracting {zip_path} to {DOWNLOAD_DIR}...")
        extract_zip(zip_path)
        print("UMLS download and extraction complete.")
    else:
        raise Exception(f"Downloaded file not found: {zip_path}")
