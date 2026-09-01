import asyncio
import json
import os
import uuid

import websockets


# room_id -> players in that room
rooms = {}

# websocket -> room_id
player_rooms = {}

# Prevent two players from joining/creating rooms
room_lock = asyncio.Lock()


async def send_json(websocket, data):
    try:
        await websocket.send(json.dumps(data))
    except websockets.exceptions.ConnectionClosed:
        pass


async def create_or_join_room(websocket):

    async with room_lock:

        # Look for a room with exactly one player
        for room_id, players in rooms.items():

            if len(players) == 1:

                players.append(websocket)
                player_rooms[websocket] = room_id

                print(
                    f"Player joined room {room_id} "
                    f"({len(players)}/2)"
                )

                # Player 1 = White
                await send_json(players[0], {
                    "Action": "StartGame",
                    "Turn": 0
                })

                # Player 2 = Black
                await send_json(players[1], {
                    "Action": "StartGame",
                    "Turn": 1
                })

                print(f"GAME STARTED in room {room_id}")

                return room_id

        # No room waiting for a player
        # Create a new room

        room_id = str(uuid.uuid4())[:8]

        rooms[room_id] = [websocket]
        player_rooms[websocket] = room_id

        print(f"Created room {room_id}")

        await send_json(websocket, {
            "Action": "WaitingForPlayer",
            "Room": room_id
        })

        return room_id


async def handle_client(websocket):

    room_id = await create_or_join_room(websocket)

    try:

        async for message in websocket:

            try:

                data = json.loads(message)

            except json.JSONDecodeError:

                print("Invalid JSON received")
                continue

            # Get this player's room
            room_id = player_rooms.get(websocket)

            if room_id is None:
                continue

            players = rooms.get(room_id)

            if players is None:
                continue

            # Make a copy so the list cannot change
            # while we're sending messages
            players_copy = players.copy()

            # =========================
            # CHAT
            # =========================

            if data.get("Action") == "Chat":

                text = str(
                    data.get("Message", "")
                ).strip()

                if not text:
                    continue

                text = text[:200]

                for client in players_copy:

                    if client != websocket:

                        await send_json(client, {
                            "Action": "Chat",
                            "Message": text
                        })

            # =========================
            # GAME
            # =========================

            else:

                for client in players_copy:

                    if client != websocket:

                        await send_json(
                            client,
                            data
                        )

    except websockets.exceptions.ConnectionClosed:

        pass

    finally:
        async with room_lock:

            room_id = player_rooms.pop(websocket, None)

            if room_id is not None:

                players = rooms.get(room_id)

                if players is not None:

                    if websocket in players:
                        players.remove(websocket)

                    print(f"Player left room {room_id}")

                    for client in players.copy():
                        await send_json(client, {
                            "Action": "OpponentDisconnected"
                        })

                    if not players:
                        rooms.pop(room_id, None)

                        print(
                            f"Deleted empty room {room_id}"
                        )

async def main():

    print("MAIN STARTED", flush=True)

    port = int(os.environ.get("PORT", 10000))

    print(f"PORT = {port}", flush=True)

    async with websockets.serve(
        handle_client,
        "0.0.0.0",
        port
    ):

        print(
            f"Server running on port {port}",
            flush=True
        )

        await asyncio.Future()


if __name__ == "__main__":
    print("STARTING SERVER FILE", flush=True)
    asyncio.run(main())
    print("SERVER STOPPED", flush=True)







# import asyncio
# import json
# import os

# import websockets

# connected_clients = []


# async def handle_client(websocket):
#     if len(connected_clients) >= 2:
#         await websocket.send(json.dumps({
#             "Action": "RoomFull"
#         }))
#         await websocket.close()
#         return

#     connected_clients.append(websocket)
#     print("Connected:", len(connected_clients))

#     if len(connected_clients) == 2:
#         await connected_clients[0].send(json.dumps({
#             "Action": "StartGame",
#             "Turn": 0
#         }))

#         await connected_clients[1].send(json.dumps({
#             "Action": "StartGame",
#             "Turn": 1
#         }))

#     try:
#         async for message in websocket:
#             try:
#                 data = json.loads(message)
#                 if data.get("Action") == "Chat":

#                     text = str(data.get("Message", "")).strip() #take down spaces
#                     if not text:
#                         continue

#                     text = text[:200]

#                     chat_message = json.dumps({
#                         "Action": "Chat",
#                         "Message": text
#                     })


#                     for client in connected_clients:
#                         if client != websocket:
#                             await client.send(chat_message)


#                 else:
#                     for client in connected_clients:
#                         if client != websocket:
#                             await client.send(message)
#             except json.JSONDecodeError:
#                 print("Invalid message received")

#     except websockets.exceptions.ConnectionClosed:
#         pass

#     finally:
#         if websocket in connected_clients:
#             connected_clients.remove(websocket)

#         for client in connected_clients:
#             await client.send(json.dumps({
#                 "Action": "OpponentDisconnected"
#             }))

#         print("Disconnected:", len(connected_clients))


# async def main():
#     port = int(os.environ.get("PORT", 10000))

#     async with websockets.serve(handle_client, "0.0.0.0", port):
#         print(f"Server running on port {port}")
#         await asyncio.Future()


# if __name__ == "__main__":
#     asyncio.run(main())