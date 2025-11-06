import base64
import json

import requests
from Crypto.Cipher import PKCS1_OAEP
from Crypto.PublicKey import RSA


class API:
    """
        This class use for control requests for server.
        Attention: This class was sync. if this class have problem we fixed problem with async, thread or multy processing.
    """

    def __init__(self, server_url: str) -> None:
        """
            Save server url to send requests
        """
        self.server_url = f"{server_url}/add-reservation/"

    def __call__(self, fullname: str, origin: str, destination: str) -> bool:
        """
            Send request to server
            :param fullname: Full name of user
            :param origin: Origin of user
            :param destination: Destination of user
            :return: True if request was successful, False otherwise
        """
        try:
            response = requests.get(self.server_url, timeout=10)
            response.raise_for_status()
            public_key = response.json()["public_key"]

            data = {
                "user_fullname": fullname,
                "origin": origin,
                "destination": destination
            }
            data = self.encoder(public_key, data)

            response = requests.post(self.server_url, data=data, timeout=10)
            response.raise_for_status()
            print(f"Sent data to server with status code: {response.status_code}")
            return True
        except requests.exceptions.RequestException as e:
            if e.response:
                print(f"Error in sending data to server: \n{e}\n{e.response.text}")
            else:
                print("Server was down. Please start server and try again.")
            return False

    @staticmethod
    def encoder(public_key, data):
        data_bytes = json.dumps(data).encode("utf-8")
        recipient_key = RSA.import_key(public_key)
        cipher_rsa = PKCS1_OAEP.new(recipient_key)
        encrypted = cipher_rsa.encrypt(data_bytes)
        encoded = base64.b64encode(encrypted)
        encoded = encoded.decode()

        data = {
            "public_key": public_key,
            "data": encoded
        }

        return data


if __name__ == '__main__':
    # Sample Test
    api = API("http://127.0.0.1:8000")
    data = {
        "fullname": "Eiliya",
        "origin": "Mashhad",
        "destination": "Tehran",
    }
    result = api(**data)
    if result:
        print("Successfully sent data to server")
    else:
        print("Failed to send data to server")
