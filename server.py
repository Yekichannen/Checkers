import asyncio
import json
import os

import websockets

connected_clients = []


async def start_game():

    if len(connected_clients) != 2:
        return

    print("Starting new game")

    await connected_clients[0].send(json.dumps({
        "Action": "StartGame",
        "Turn": 0
    }))

    await connected_clients[1].send(json.dumps({
        "Action": "StartGame",
        "Turn": 1
    }))


async def handle_client(websocket):

    # Room already has 2 players
    if len(connected_clients) >= 2:

        await websocket.send(json.dumps({
            "Action": "RoomFull"
        }))

        await websocket.close()

        return


    # Add player
    connected_clients.append(websocket)

    print("Connected:", len(connected_clients))


    # If this is the second player, start the game
    if len(connected_clients) == 2:

        await start_game()


    try:

        async for message in websocket:

            try:

                data = json.loads(message)


                # CHAT
                if data.get("Action") == "Chat":

                    text = str(
                        data.get("Message", "")
                    ).strip()

                    if not text:
                        continue

                    text = text[:200]

                    chat_message = json.dumps({
                        "Action": "Chat",
                        "Message": text
                    })


                    for client in connected_clients:

                        if client != websocket:

                            await client.send(chat_message)


                # EVERYTHING ELSE
                else:

                    for client in connected_clients:

                        if client != websocket:

                            await client.send(message)


            except json.JSONDecodeError:

                print("Invalid message received")


    except websockets.exceptions.ConnectionClosed:

        pass


    finally:

        if websocket in connected_clients:

            connected_clients.remove(websocket)


        print("Disconnected:", len(connected_clients))


        # Tell remaining player
        for client in connected_clients:

            try:

                await client.send(json.dumps({
                    "Action": "OpponentDisconnected"
                }))

            except websockets.exceptions.ConnectionClosed:

                pass


async def main():

    port = int(
        os.environ.get("PORT", 10000)
    )


    async with websockets.serve(
        handle_client,
        "0.0.0.0",
        port
    ):

        print(
            f"Server running on port {port}"
        )

        await asyncio.Future()


if __name__ == "__main__":

    asyncio.run(main())