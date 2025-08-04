import boto3
from datetime import datetime
from playwright.sync_api import Page
from settings import AWS_REGION, S3_BUCKET

def capture_and_upload_screenshot(
    page: Page,
    backend: str,
    task_id: str,
    account_id=None,
) -> str:
    """
    Takes a screenshot (PNG bytes) of the current page, uploads to S3,
    and returns the public URL.
    """
    # 1) Capture screenshot bytes
    png_bytes = page.screenshot(full_page=True)  # returns bytes

    # 2) Build a timestamped S3 key
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    key = f"screenshots/{backend}/{account_id}_{task_id}_{now}.png"

    # 3) Upload via boto3
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=png_bytes,
        ContentType="image/png",
        ACL="private"  # or "public-read" if you really need public URLs
    )

    # 4) Construct URL (adjust if you use custom domain / CloudFront)
    url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{key}"
    return url
