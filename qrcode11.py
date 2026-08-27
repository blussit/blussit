import qrcode
from PIL import Image


def generate_qr_with_logo(url, logo_path, output_path="qr_code.png"):
    # Create QR code
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=4
    )

    qr.add_data(url)
    qr.make(fit=True)

    # Generate QR image
    qr_image = qr.make_image(
        fill_color="black",
        back_color="white"
    ).convert("RGB")

    # Open logo
    logo = Image.open(logo_path).convert("RGBA")

    # Resize logo
    qr_width, qr_height = qr_image.size

    logo_size = qr_width // 5
    logo.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)

    # Create white background behind logo
    padding = 15

    logo_background = Image.new(
        "RGBA",
        (
            logo.width + padding * 2,
            logo.height + padding * 2
        ),
        "white"
    )

    # Center logo on white background
    logo_x = (logo_background.width - logo.width) // 2
    logo_y = (logo_background.height - logo.height) // 2

    logo_background.alpha_composite(
        logo,
        (logo_x, logo_y)
    )

    # Calculate center position
    position = (
        (qr_width - logo_background.width) // 2,
        (qr_height - logo_background.height) // 2
    )

    # Place logo in center
    qr_image.paste(
        logo_background,
        position,
        logo_background
    )

    # Save QR code
    qr_image.save(output_path)

    print(f"QR code generated successfully!")
    print(f"Saved as: {output_path}")


# --------------------------------
# User Input
# --------------------------------

url = input("Enter the URL: ").strip()
logo_path = input("Enter the logo image path: ").strip()

generate_qr_with_logo(
    url=url,
    logo_path=logo_path,
    output_path="qr_code_with_logo.png"
)