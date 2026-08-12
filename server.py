import asyncio
import websockets
import json
import os

connected_clients = []

async def handle_client(websocket):

    connected_clients.append(websocket)

    print("Connected:", len(connected_clients))

    # start game ONLY when 2 players connected
    if len(connected_clients) == 2:

        await connected_clients[0].send(
            json.dumps({
                "Action": "StartGame",
                "Turn": 0
            })
        )

        await connected_clients[1].send(
            json.dumps({
                "Action": "StartGame",
                "Turn": 1
            })
        )

    try:

        async for message in websocket:

            for client in connected_clients:

                if client != websocket:

                    await client.send(message)

    except websockets.exceptions.ConnectionClosed:
        pass

    finally:

        if websocket in connected_clients:
            connected_clients.remove(websocket)

        print(
            "Disconnected:",
            len(connected_clients)
        )


async def main():

    async with websockets.serve(
    handle_client,
    "0.0.0.0",
    int(os.environ.get("PORT", 10000))
    ):

        print("Server running")

        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())