import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid

import asyncpg
import websockets
from websockets.exceptions import ConnectionClosed


# Render Environment Variable:
# DATABASE_URL = your Supabase connection string
databaseUrl = os.environ.get("DATABASE_URL")

pool = None

rooms = {}
player_rooms = {}
room_lock = asyncio.Lock()

USERNAME_RULE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


def hashPassword(password):
    salt = secrets.token_bytes(16)

    passwordHash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1
    )

    return (
        base64.b64encode(salt).decode("utf-8")
        + "$"
        + base64.b64encode(passwordHash).decode("utf-8")
    )


def correctPassword(password, savedHash):
    try:
        saltText, hashText = savedHash.split("$", 1)

        salt = base64.b64decode(saltText)
        realHash = base64.b64decode(hashText)

        testHash = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1
        )

        return hmac.compare_digest(testHash, realHash)

    except Exception:
        return False


async def sendJson(player, data):
    try:
        await player.send(json.dumps(data))
    except ConnectionClosed:
        pass


async def registerPlayer(player, data):
    username = str(data.get("Username", "")).strip()
    password = str(data.get("Password", ""))

    if not USERNAME_RULE.fullmatch(username):
        await sendJson(player, {
            "Action": "RegisterResult",
            "Success": False,
            "Message": "Username: 3-20 letters, numbers, or _ only."
        })
        return

    if len(password) < 8:
        await sendJson(player, {
            "Action": "RegisterResult",
            "Success": False,
            "Message": "Password must have at least 8 characters."
        })
        return

    try:
        await pool.execute(
            """
            INSERT INTO users (username, password_hash)
            VALUES ($1, $2)
            """,
            username,
            hashPassword(password)
        )

        await sendJson(player, {
            "Action": "RegisterResult",
            "Success": True,
            "Message": "Account created. You can log in now."
        })

    except asyncpg.UniqueViolationError:
        await sendJson(player, {
            "Action": "RegisterResult",
            "Success": False,
            "Message": "That username is already taken."
        })


async def loginPlayer(player, data):
    username = str(data.get("Username", "")).strip()
    password = str(data.get("Password", ""))

    user = await pool.fetchrow(
        """
        SELECT password_hash
        FROM users
        WHERE username = $1
        """,
        username
    )

    if user is None or not correctPassword(password, user["password_hash"]):
        await sendJson(player, {
            "Action": "LoginResult",
            "Success": False,
            "Message": "Wrong username or password."
        })
        return None

    await sendJson(player, {
        "Action": "LoginResult",
        "Success": True,
        "Username": username,
        "Message": f"Welcome, {username}!"
    })

    return username


async def findGame(player):
    async with room_lock:

        for roomId, players in rooms.items():

            if len(players) == 1:
                players.append(player)
                player_rooms[player] = roomId

                await sendJson(players[0], {
                    "Action": "StartGame",
                    "Turn": 0
                })

                await sendJson(players[1], {
                    "Action": "StartGame",
                    "Turn": 1
                })

                print(f"Game started: {roomId}")
                return

        roomId = str(uuid.uuid4())[:8]

        rooms[roomId] = [player]
        player_rooms[player] = roomId

        await sendJson(player, {
            "Action": "WaitingForPlayer",
            "Room": roomId
        })

        print(f"Waiting room created: {roomId}")


async def removePlayerFromGame(player):
    async with room_lock:

        roomId = player_rooms.pop(player, None)

        if roomId is None:
            return

        players = rooms.get(roomId)

        if players is None:
            return

        if player in players:
            players.remove(player)

        for otherPlayer in players.copy():
            await sendJson(otherPlayer, {
                "Action": "OpponentDisconnected"
            })

        if len(players) == 0:
            rooms.pop(roomId, None)


async def handleClient(player):
    username = None

    try:
        async for message in player:

            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            action = data.get("Action")

            # CREATE ACCOUNT

            if action == "Register":
                await registerPlayer(player, data)
                continue

            # LOG IN

            if action == "Login":
                username = await loginPlayer(player, data)
                continue

            # Do not let someone play before logging in.

            if username is None:
                await sendJson(player, {
                    "Action": "Error",
                    "Message": "Log in before playing online."
                })
                continue

            # FIND OPPONENT

            if action == "FindGame":
                await findGame(player)
                continue

            # SEND CHAT AND MOVES TO OPPONENT

            roomId = player_rooms.get(player)

            if roomId is None:
                continue

            players = rooms.get(roomId, [])

            for otherPlayer in players.copy():
                if otherPlayer != player:
                    await sendJson(otherPlayer, data)

    except ConnectionClosed:
        pass

    finally:
        await removePlayerFromGame(player)


async def createDatabaseTables():
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username VARCHAR(20) PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)


async def main():
    global pool

    if not databaseUrl:
        print("DATABASE_URL is missing.")
        return

    pool = await asyncpg.create_pool(
        databaseUrl,
        statement_cache_size=0
    )

    await createDatabaseTables()

    port = int(os.environ.get("PORT", 10000))

    print(f"Server running on port {port}")

    async with websockets.serve(
        handleClient,
        "0.0.0.0",
        port
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())








# # These are Python modules that give us tools we need for the server.

# import asyncio      # Lets Python handle multiple things happening at once
# import json         # Lets us convert Python data to/from JSON
# import os           # Lets us get information from the computer/server
# import uuid         # Lets us create unique IDs

# import websockets   # Lets us create a WebSocket server


# # ROOMS

# # A dictionary that stores all the game rooms.

# # The format looks like:
# #
# # rooms = {
# #     "abc12345": [player1, player2],
# #     "xyz67890": [player1]
# # }
# #
# # The room ID is the key.
# # The list contains the players currently inside that room.

# rooms = {}


# # This dictionary tells us which room each player is in.

# # The format looks like:
# #
# # player_rooms[player1] = "abc12345"
# #
# # This makes it easy to find a player's room later.

# player_rooms = {}


# # A lock prevents two players from trying to create/join
# # a room at exactly the same time.

# # Without this, two players could potentially cause
# # problems while the rooms dictionary is being changed.

# room_lock = asyncio.Lock()


# # ============================================================
# # SEND JSON
# # ============================================================

# async def send_json(websocket, data):

#     # Try to send a message to the player.
#     try:

#         # Convert the Python dictionary into JSON text
#         # and send it through the WebSocket.
#         await websocket.send(json.dumps(data))

#     # If the player disconnected while we were sending,
#     # don't crash the server.
#     except websockets.exceptions.ConnectionClosed:

#         pass


# # CREATE OR JOIN ROOM

# async def create_or_join_room(websocket):

#     # Lock the room system so only one player at a time
#     # can create or join a room.
#     async with room_lock:

#         # Look through every room currently on the server.
#         for room_id, players in rooms.items():

#             # If the room has exactly one player,
#             # this player can join it.
#             if len(players) == 1:

#                 # Add the new player to the room.
#                 players.append(websocket)

#                 # Remember which room this player belongs to.
#                 player_rooms[websocket] = room_id


#                 # Print information in the server console.
#                 print(
#                     f"Player joined room {room_id} "
#                     f"({len(players)}/2)"
#                 )


#                 # Player 1 gets White.
#                 #
#                 # Turn 0 = White
#                 await send_json(players[0], {
#                     "Action": "StartGame",
#                     "Turn": 0
#                 })


#                 # Player 2 gets Black.
#                 #
#                 # Turn 1 = Black
#                 await send_json(players[1], {
#                     "Action": "StartGame",
#                     "Turn": 1
#                 })


#                 # Tell us in the server console
#                 # that the game has started.
#                 print(f"GAME STARTED in room {room_id}")


#                 # Return the room ID because we are finished.
#                 return room_id


#         # NO ROOM WAS AVAILABLE

#         # If we reach this point, there wasn't a room
#         # waiting for another player.

#         # So we create a new room.


#         # Create a random unique ID for the room.
#         #
#         # uuid.uuid4() creates a very long unique ID.
#         # [:8] takes only the first 8 characters.
#         room_id = str(uuid.uuid4())[:8]


#         # Create the room and put this player inside it.
#         rooms[room_id] = [websocket]


#         # Remember which room this player belongs to.
#         player_rooms[websocket] = room_id


#         # Print the new room ID in the server console.
#         print(f"Created room {room_id}")


#         # Tell the player that they are waiting
#         # for another player.
#         await send_json(websocket, {
#             "Action": "WaitingForPlayer",
#             "Room": room_id
#         })


#         # Return the room ID.
#         return room_id


# # HANDLE CLIENT

# async def handle_client(websocket):

#     # When a player connects, create a new room for them
#     # or put them into a room that already has one player.
#     room_id = await create_or_join_room(websocket)


#     # Keep listening for messages from this player.
#     #
#     # "async for" means:
#     # keep getting messages until the player disconnects.
#     try:

#         async for message in websocket:

#             # Try to turn the received JSON text
#             # into a Python object.
#             try:

#                 data = json.loads(message)

#             # If the player sent something that isn't valid JSON,
#             # don't crash the server.
#             except json.JSONDecodeError:

#                 print("Invalid JSON received")
#                 continue


#             # FIND PLAYER'S ROOM

#             # Find which room this player is currently in.
#             room_id = player_rooms.get(websocket)


#             # If we couldn't find their room,
#             # ignore the message.
#             if room_id is None:
#                 continue


#             # Get the list of players in that room.
#             players = rooms.get(room_id)


#             # If the room doesn't exist,
#             # ignore the message.
#             if players is None:
#                 continue


#             # Make a copy of the players list.
#             #
#             # This is safer because the original list might
#             # change while we are sending messages.
#             players_copy = players.copy()


#             # =================================================
#             # CHAT
#             # =================================================

#             # Check if the message is a chat message.
#             if data.get("Action") == "Chat":

#                 # Get the message text.
#                 #
#                 # str() makes sure it is treated as text.
#                 # strip() removes spaces from the beginning/end.
#                 text = str(
#                     data.get("Message", "")
#                 ).strip()


#                 # If there is no message,
#                 # don't send anything.
#                 if not text:
#                     continue


#                 # Only allow the first 200 characters.
#                 #
#                 # This prevents someone from sending
#                 # an extremely large chat message.
#                 text = text[:200]


#                 # Send the chat message to every player
#                 # except the person who sent it.
#                 for client in players_copy:

#                     if client != websocket:

#                         await send_json(client, {
#                             "Action": "Chat",
#                             "Message": text
#                         })


#             # =================================================
#             # GAME
#             # =================================================

#             # If the message wasn't a chat message,
#             # treat it as a game message.
#             #
#             # For example:
#             # Move
#             # Board updates
#             # Turn changes
#             # Scores
#             else:

#                 # Send the game message to every other
#                 # player in the room.
#                 for client in players_copy:

#                     if client != websocket:

#                         await send_json(
#                             client,
#                             data
#                         )


#     # PLAYER DISCONNECTED

#     # If the player disconnects, WebSockets raises
#     # ConnectionClosed.
#     except websockets.exceptions.ConnectionClosed:

#         # We don't need to do anything here because
#         # the "finally" section below will clean everything up.
#         pass


#     # CLEAN UP AFTER DISCONNECT

#     # "finally" always runs when the player leaves,
#     # whether there was an error or not.
#     finally:

#         # Lock the room system so another player
#         # cannot change the rooms at the same time.
#         async with room_lock:

#             # Remove this player from player_rooms.
#             #
#             # pop() also gives us the room ID they were in.
#             #
#             # None means "nothing was found".
#             room_id = player_rooms.pop(websocket, None)


#             # If the player actually belonged to a room:
#             if room_id is not None:

#                 # Get that room.
#                 players = rooms.get(room_id)


#                 # Make sure the room still exists.
#                 if players is not None:

#                     # Remove the disconnected player
#                     # from the room's player list.
#                     if websocket in players:
#                         players.remove(websocket)


#                     # Print information in the server console.
#                     print(f"Player left room {room_id}")


#                     # Tell the remaining players
#                     # that their opponent disconnected.
#                     #
#                     # .copy() makes a safe copy of the list.
#                     for client in players.copy():

#                         await send_json(client, {
#                             "Action": "OpponentDisconnected"
#                         })


#                     # If nobody is left in the room,
#                     # delete the room.
#                     if not players:

#                         rooms.pop(room_id, None)


#                         # Print that the room was deleted.
#                         print(
#                             f"Deleted empty room {room_id}"
#                         )


# # MAIN SERVER

# async def main():

#     # Tell us in the server console
#     # that the main server function started.
#     #
#     # flush=True makes the message appear immediately.
#     print("MAIN STARTED", flush=True)


#     # Get the port number from the server environment.

#     # Render gives your server a PORT value.
#     #
#     # If there isn't one, use port 10000.
#     port = int(os.environ.get("PORT", 10000))


#     # Show which port the server is using.
#     print(f"PORT = {port}", flush=True)


#     # Start the WebSocket server.
#     async with websockets.serve(
#         handle_client,       # Function that handles each player
#         "0.0.0.0",           # Allow connections from anywhere
#         port                 # Port the server should use
#     ):


#         # Tell us that the server is running.
#         print(
#             f"Server running on port {port}",
#             flush=True
#         )


#         # Keep the server running forever.
#         #
#         # asyncio.Future() creates something that
#         # never finishes, so the server stays alive.
#         await asyncio.Future()


# # START THE PROGRAM

# # This checks whether this file was run directly.

# # If it was, start the async main() function.
# if __name__ == "__main__":

#     # Start the server using asyncio.
#     asyncio.run(main())
