from PIL import Image


class PillowGifFrameExtractor:
    """Produces one stable GIF frame for single-raster analysis providers."""

    def extract(self, image: Image.Image) -> Image.Image:
        image.seek(0)
        return image.copy()
