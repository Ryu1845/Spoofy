from cryptography.fernet import Fernet, InvalidToken


class EncryptionTool:
    class InvalidKey(Exception):
        pass

    @classmethod
    def generate(cls):
        return Fernet.generate_key()

    def __init__(self, key):
        self.suite = Fernet(key)

    def encrypt(self, message: str) -> str:
        return self.suite.encrypt(message.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self.suite.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            raise EncryptionTool.InvalidKey()
