import base64
import hashlib
import re
from typing import Dict, Optional, TextIO, Tuple, Union


class AirplayMetadataProcessor:
    """
    Processes metadata from an named pipe, extracting track information,
    album art, and other details, while detecting changes to the metadata.
    """
    def __init__(self) -> None:
        """Initialise the current and previous metadata dictionaries."""
        self.meta_data = {
            'track': '',
            'album': '',
            'artist': '',
            'filetype': '',
            'md5': '',
            'filename': ''
        }
        self.old_meta_data = self.meta_data.copy()

    def start_item(self, line: str) -> Tuple[str, str, int]:
        """
        Parses the XML <item> tag to extract type, code, and length.

        :param line: A string containing the XML line.
        :return: A tuple containing the type, code, and length.
        """
        regex = r"<item><type>([A-Fa-f0-9]{8})</type><code>([A-Fa-f0-9]{8})</code><length>(\d+)</length>"
        matches = re.findall(regex, line)
        if matches:
            item_type = bytes.fromhex(matches[0][0]).decode()
            item_code = bytes.fromhex(matches[0][1]).decode()
            item_length = int(matches[0][2])
            return item_type, item_code, item_length
        return "", "", 0

    def start_data(self, line: str) -> bool:
        """
        Checks if the line indicates the start of base64 encoded data.

        :param line: A string containing the XML line.
        :return: True if data encoding is base64, otherwise False.
        """
        return line.startswith('<data encoding="base64">')

    def read_data(self, line: str, decode: bool) -> Union[str, bytes]:
        """
        Decodes the base64 encoded data from the line.

        :param line: A string containing base64 encoded data.
        :param decode: Boolean flag indicating if the data should be decoded into a string.
        :return: Decoded data as a string or bytes.
        """
        try:
            data = base64.b64decode(line.split('</data>')[0])
            return data.decode() if decode else data
        except Exception:
            return ""

    def guess_image_mime(self, magic: bytes) -> str:
        """
        Guesses the MIME type of an image based on its magic number.

        :param magic: The first few bytes of the image data.
        :return: The image MIME type ('jpg' or 'png').
        """
        if magic.startswith(b'\xff\xd8'):
            return 'jpg'
        if magic.startswith(b'\x89PNG\r\n\x1a\r'):
            return 'png'
        return 'jpg'

    def update_metadata(self) -> Optional[Dict[str, str]]:
        """
        Updates and returns metadata if there are any changes.

        :return: Updated metadata dictionary if changed, otherwise None.
        """
        if any(self.old_meta_data[item] != self.meta_data[item] for item in self.meta_data):
            self.old_meta_data.update(self.meta_data)
            return self.meta_data
        return None

    def process_line(self, line: str, pipe: TextIO) -> Optional[Dict[str, str]]:
        """
        Processes a line of XML metadata, updating internal metadata state.

        :param line: A string containing the XML line.
        :param pipe: A file-like object representing the named pipe.
        :return: Updated metadata if significant changes are detected, otherwise None.
        """
        if not line.startswith("<item>"):
            return None

        item_type, item_code, item_length = self.start_item(line)
        data = ""

        if item_length > 0 and self.start_data(pipe.readline()):
            data = self.read_data(pipe.readline(), item_type != "ssnc" or item_code != "PICT")

        if item_type == "core":
            if item_code == "asal":
                self.meta_data['album'] = data
            elif item_code == "asar":
                self.meta_data['artist'] = data
            elif item_code == "minm":
                self.meta_data['track'] = data

        if item_type == "ssnc" and item_code == "PICT" and data:
            file_type = self.guess_image_mime(data)
            filename = f'/tmp/shairport-image.{file_type}'
            with open(filename, 'wb') as file:
                file.write(data)
            self.meta_data.update({
                'filetype': file_type,
                'md5': hashlib.md5(data).hexdigest(),
                'filename': filename
            })

        if item_type == "ssnc" and item_code in {"pfls", "pend", "mden"}:
            return self.update_metadata()

        return None
