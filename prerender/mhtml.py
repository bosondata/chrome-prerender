import base64
import quopri
from email.message import EmailMessage


class MHTML(object):
    def __init__(self):
        self._msg = EmailMessage()
        self._msg['MIME-Version'] = '1.0'
        self._msg.add_header('Content-Type', 'multipart/related', type='text/html')

    def add(self, location: str, content_type: str, payload: str, encoding: str = 'quoted-printable') -> None:
        resource = EmailMessage()
        if content_type == 'text/html':
            resource.add_header('Content-Type', 'text/html', charset='utf-8')
        else:
            resource['Content-Type'] = content_type
        if encoding == 'quoted-printable':
            resource['Content-Transfer-Encoding'] = encoding
            resource.set_payload(quopri.encodestring(payload.encode()))
        elif encoding == 'base64':
            resource['Content-Transfer-Encoding'] = encoding
            resource.set_payload(base64.b64encode(payload))
        elif encoding == 'base64-encoded':  # Already base64 encoded
            resource['Content-Transfer-Encoding'] = 'base64'
            resource.set_payload(payload)
        else:
            raise ValueError('invalid encoding')
        resource['Content-Location'] = location
        self._msg.attach(resource)

    def __str__(self) -> str:
        return str(self._msg)

    def __bytes__(self) -> bytes:
        return bytes(self._msg)
