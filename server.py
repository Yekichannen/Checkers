import asyncio
import websockets

connected_clients = []


async def handle_client(websocket):

    connected_clients.append(websocket)

    print("Connected:", len(connected_clients))

    try:

        async for message in websocket:

            print("Received:", message)

            # send to all OTHER players
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
        "localhost",
        8765
    ):

        print(
            "Server running on ws://localhost:8765"
        )

        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())