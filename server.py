# ============================================================
# CHECKERS ONLINE SERVER
#
# Features:
# - Account registration
# - Login
# - Supabase database storage
# - Online matchmaking
# - Move forwarding
# - Chat forwarding
# ============================================================

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


# ============================================================
# SUPABASE / RENDER
# ============================================================

databaseUrl = os.environ.get("DATABASE_URL")
pool = None


# ============================================================
# ONLINE ROOM DATA
# ============================================================

# rooms = {"abc12345": [firstPlayer, secondPlayer]}
rooms = {}

# player_rooms[playerSocket] = "abc12345"
player_rooms = {}

room_lock = asyncio.Lock()


# ============================================================
# USERNAME RULE
# ============================================================

USERNAME_RULE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


# ============================================================
# PASSWORD HASHING
# ============================================================

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


# ============================================================
# SEND JSON TO PLAYER
# ============================================================

async def sendJson(player, data):
    try:
        await player.send(json.dumps(data))
    except ConnectionClosed:
        pass


# ============================================================
# CREATE ACCOUNT
# ============================================================

async def registerPlayer(player, data):
    username = str(data.get("Username", "")).strip()
    password = str(data.get("Password", ""))

    if not USERNAME_RULE.fullmatch(username):
        await sendJson(player, {
            "Action": "RegisterResult",
            "Success": False,
            "Message": "Username: use 3-20 letters, numbers, or _ only."
        })
        return

    if len(password) < 8:
        await sendJson(player, {
            "Action": "RegisterResult",
            "Success": False,
            "Message": "Password must contain at least 8 characters."
        })
        return

    try:
        await pool.execute(
            "INSERT INTO users (username, password_hash) VALUES ($1, $2)",
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
    except Exception as error:
        print("Registration error:", error)
        await sendJson(player, {
            "Action": "RegisterResult",
            "Success": False,
            "Message": "Could not create account. Try again."
        })


# ============================================================
# LOGIN
# ============================================================

async def loginPlayer(player, data):
    username = str(data.get("Username", "")).strip()
    password = str(data.get("Password", ""))

    user = await pool.fetchrow(
        "SELECT password_hash FROM users WHERE username = $1",
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


# ============================================================
# FIND OR CREATE GAME ROOM
# ============================================================

async def findGame(player):
    async with room_lock:
        if player in player_rooms:
            await sendJson(player, {
                "Action": "Error",
                "Message": "You are already waiting or playing."
            })
            return

        for roomId, players in rooms.items():
            if len(players) == 1:
                players.append(player)
                player_rooms[player] = roomId

                await sendJson(players[0], {"Action": "StartGame", "Turn": 0})
                await sendJson(players[1], {"Action": "StartGame", "Turn": 1})
                print(f"Game started in room {roomId}")
                return

        roomId = str(uuid.uuid4())[:8]
        rooms[roomId] = [player]
        player_rooms[player] = roomId

        await sendJson(player, {
            "Action": "WaitingForPlayer",
            "Room": roomId
        })
        print(f"Created waiting room {roomId}")


# ============================================================
# REMOVE DISCONNECTED PLAYER
# ============================================================

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
            await sendJson(otherPlayer, {"Action": "OpponentDisconnected"})

        if len(players) == 0:
            rooms.pop(roomId, None)

        print(f"Player left room {roomId}")


# ============================================================
# HANDLE ONE CONNECTED BROWSER
# ============================================================

async def handleClient(player):
    username = None
    try:
        async for message in player:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            action = data.get("Action")

            if action == "Register":
                await registerPlayer(player, data)
                continue

            if action == "Login":
                username = await loginPlayer(player, data)
                continue

            if username is None:
                await sendJson(player, {
                    "Action": "Error",
                    "Message": "Log in before playing online."
                })
                continue

            if action == "FindGame":
                await findGame(player)
                continue

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


# ============================================================
# CREATE SUPABASE USERS TABLE
# ============================================================

async def createDatabaseTable():
    await pool.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "username VARCHAR(20) PRIMARY KEY, "
        "password_hash TEXT NOT NULL, "
        "created_at TIMESTAMPTZ DEFAULT NOW()"
        ")"
    )


# ============================================================
# START SERVER
# ============================================================

async def main():
    global pool

    if not databaseUrl:
        print("DATABASE_URL is missing in Render.")
        return

    pool = await asyncpg.create_pool(
        databaseUrl,
        statement_cache_size=0
    )

    await createDatabaseTable()

    port = int(os.environ.get("PORT", 10000))
    print(f"Server running on port {port}")

    async with websockets.serve(handleClient, "0.0.0.0", port):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())


















# # ============================================================
# # CHECKERS ONLINE SERVER
# #
# # This server:
# # - creates accounts with username + password
# # - logs players in
# # - saves account information in Supabase
# # - pairs two logged-in players into a game
# # - forwards moves and chat messages between opponents
# # ============================================================


# # ============================================================
# # CHECKERS ONLINE SERVER
# #
# # Features:
# # - Account registration
# # - Login
# # - Supabase database storage
# # - Online matchmaking
# # - Move forwarding
# # - Chat forwarding
# # ============================================================


# # asyncio lets the server handle many players at once.
# import asyncio

# # base64 turns binary password data into text that can be saved in PostgreSQL.
# import base64

# # hashlib gives us scrypt, used to hash passwords safely.
# import hashlib

# # hmac gives us a safe way to compare password hashes.
# import hmac

# # json changes Python dictionaries into text and back again.
# import json

# # os lets us read Render environment variables.
# import os

# # re lets us check username rules with a pattern.
# import re

# # secrets creates cryptographically secure random salt values.
# import secrets

# # uuid creates unique random room IDs.
# import uuid


# # asyncpg connects Python to Supabase PostgreSQL.
# import asyncpg

# # websockets creates the online server.
# import websockets

# # This error happens when a browser closes its WebSocket connection.
# from websockets.exceptions import ConnectionClosed


# # ============================================================
# # SUPABASE / RENDER
# # ============================================================

# # Render reads this from Environment Variables.
# #
# # Key: DATABASE_URL
# # Value: your private Supabase connection string
# databaseUrl = os.environ.get("DATABASE_URL")


# # This becomes the Supabase connection pool after startup.
# pool = None


# # ============================================================
# # ONLINE ROOM DATA
# # ============================================================

# # Example:
# #
# # rooms = {
# #     "abc12345": [firstPlayer, secondPlayer]
# # }
# rooms = {}


# # Example:
# #
# # player_rooms[playerSocket] = "abc12345"
# player_rooms = {}


# # Prevents two players from changing rooms simultaneously.
# room_lock = asyncio.Lock()


# # Username must have:
# # - 3 to 20 characters
# # - letters, numbers, or _
# USERNAME_RULE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


# # ============================================================
# # PASSWORD HASHING
# # ============================================================

# def hashPassword(password):

#     # Create random salt.
#     #
#     # A salt makes matching passwords create different hashes.
#     salt = secrets.token_bytes(16)

#     # Convert password into a secure scrypt hash.
#     passwordHash = hashlib.scrypt(
#         password.encode("utf-8"),
#         salt=salt,
#         n=2**14,
#         r=8,
#         p=1
#     )

#     # Save salt and hash together as text.
#     return (
#         base64.b64encode(salt).decode("utf-8")
#         + "$"
#         + base64.b64encode(passwordHash).decode("utf-8")
#     )


# def correctPassword(password, savedHash):

#     try:
#         # Split saved text into salt and password hash.
#         saltText, hashText = savedHash.split("$", 1)

#         # Turn saved text back into bytes.
#         salt = base64.b64decode(saltText)
#         realHash = base64.b64decode(hashText)

#         # Hash the newly entered password using saved salt.
#         testHash = hashlib.scrypt(
#             password.encode("utf-8"),
#             salt=salt,
#             n=2**14,
#             r=8,
#             p=1
#         )

#         # Return True if hashes match.
#         return hmac.compare_digest(testHash, realHash)

#     except Exception:
#         # If saved password data is broken, login fails.
#         return False


# # ============================================================
# # SEND JSON TO PLAYER
# # ============================================================

# async def sendJson(player, data):

#     try:
#         # Turn Python dictionary into JSON text and send it.
#         await player.send(json.dumps(data))

#     except ConnectionClosed:
#         # Do nothing if player disconnected.
#         pass


# # ============================================================
# # CREATE ACCOUNT
# # ============================================================

# async def registerPlayer(player, data):

#     # Get username from browser message.
#     username = str(data.get("Username", "")).strip()

#     # Get password from browser message.
#     password = str(data.get("Password", ""))

#     # Reject invalid username.
#     if not USERNAME_RULE.fullmatch(username):
#         await sendJson(player, {
#             "Action": "RegisterResult",
#             "Success": False,
#             "Message": "Username: use 3-20 letters, numbers, or _ only."
#         })
#         return

#     # Reject short password.
#     if len(password) < 8:
#         await sendJson(player, {
#             "Action": "RegisterResult",
#             "Success": False,
#             "Message": "Password must contain at least 8 characters."
#         })
#         return

#     try:
#         # Save username and password hash in Supabase.
#         await pool.execute(
#             "INSERT INTO users (username, password_hash) "
#             "VALUES ($1, $2)",
#             username,
#             hashPassword(password)
#         )

#         # Tell browser account was created.
#         await sendJson(player, {
#             "Action": "RegisterResult",
#             "Success": True,
#             "Message": "Account created. You can log in now."
#         })

#     except asyncpg.UniqueViolationError:
#         # This username already exists.
#         await sendJson(player, {
#             "Action": "RegisterResult",
#             "Success": False,
#             "Message": "That username is already taken."
#         })

#     except Exception as error:
#         # Print real error only in Render logs.
#         print("Registration error:", error)

#         await sendJson(player, {
#             "Action": "RegisterResult",
#             "Success": False,
#             "Message": "Could not create account. Try again."
#         })


# # ============================================================
# # LOGIN
# # ============================================================

# async def loginPlayer(player, data):

#     # Get username and password sent from browser.
#     username = str(data.get("Username", "")).strip()
#     password = str(data.get("Password", ""))

#     # Find account by username.
#     user = await pool.fetchrow(
#         "SELECT password_hash "
#         "FROM users "
#         "WHERE username = $1",
#         username
#     )

#     # Fail if username does not exist or password is wrong.
#     if user is None or not correctPassword(password, user["password_hash"]):
#         await sendJson(player, {
#             "Action": "LoginResult",
#             "Success": False,
#             "Message": "Wrong username or password."
#         })
#         return None

#     # Tell browser login succeeded.
#     await sendJson(player, {
#         "Action": "LoginResult",
#         "Success": True,
#         "Username": username,
#         "Message": f"Welcome, {username}!"
#     })

#     # Give logged-in username back to handleClient.
#     return username


# # ============================================================
# # FIND OR CREATE GAME ROOM
# # ============================================================

# async def findGame(player):

#     # Lock room system.
#     async with room_lock:

#         # Stop player joining twice.
#         if player in player_rooms:
#             await sendJson(player, {
#                 "Action": "Error",
#                 "Message": "You are already waiting or playing."
#             })
#             return

#         # Look at every room.
#         for roomId, players in rooms.items():

#             # Room with one player is waiting for opponent.
#             if len(players) == 1:

#                 # Add player as second player.
#                 players.append(player)

#                 # Remember this player's room.
#                 player_rooms[player] = roomId

#                 # First player becomes White.
#                 await sendJson(players[0], {
#                     "Action": "StartGame",
#                     "Turn": 0
#                 })

#                 # Second player becomes Black.
#                 await sendJson(players[1], {
#                     "Action": "StartGame",
#                     "Turn": 1
#                 })

#                 print(f"Game started in room {roomId}")
#                 return

#         # No waiting player exists.

#         # Create new room ID.
#         roomId = str(uuid.uuid4())[:8]

#         # Put player in new room.
#         rooms[roomId] = [player]

#         # Remember where player is.
#         player_rooms[player] = roomId

#         # Tell browser to wait.
#         await sendJson(player, {
#             "Action": "WaitingForPlayer",
#             "Room": roomId
#         })

#         print(f"Created waiting room {roomId}")


# # ============================================================
# # REMOVE DISCONNECTED PLAYER
# # ============================================================

# async def removePlayerFromGame(player):

#     # Lock room system.
#     async with room_lock:

#         # Remove player from player_rooms.
#         #
#         # roomId becomes None if player was not in a room.
#         roomId = player_rooms.pop(player, None)

#         if roomId is None:
#             return

#         # Find room.
#         players = rooms.get(roomId)

#         if players is None:
#             return

#         # Remove player from room.
#         if player in players:
#             players.remove(player)

#         # Tell remaining opponent they disconnected.
#         for otherPlayer in players.copy():
#             await sendJson(otherPlayer, {
#                 "Action": "OpponentDisconnected"
#             })

#         # Delete completely empty rooms.
#         if len(players) == 0:
#             rooms.pop(roomId, None)

#         print(f"Player left room {roomId}")


# # ============================================================
# # HANDLE ONE CONNECTED BROWSER
# # ============================================================

# async def handleClient(player):

#     # Player has not logged in at first.
#     username = None

#     try:
#         # Keep receiving messages until player disconnects.
#         async for message in player:

#             try:
#                 # Change received JSON text into Python dictionary.
#                 data = json.loads(message)

#             except json.JSONDecodeError:
#                 # Ignore broken JSON.
#                 continue

#             # Read Action from JSON.
#             action = data.get("Action")

#             # ------------------------------------------------
#             # REGISTER
#             # ------------------------------------------------

#             if action == "Register":
#                 await registerPlayer(player, data)
#                 continue

#             # ------------------------------------------------
#             # LOGIN
#             # ------------------------------------------------

#             if action == "Login":
#                 username = await loginPlayer(player, data)
#                 continue

#             # ------------------------------------------------
#             # BLOCK ONLINE ACTIONS UNTIL LOGIN
#             # ------------------------------------------------

#             if username is None:
#                 await sendJson(player, {
#                     "Action": "Error",
#                     "Message": "Log in before playing online."
#                 })
#                 continue

#             # ------------------------------------------------
#             # FIND OPPONENT
#             # ------------------------------------------------

#             if action == "FindGame":
#                 await findGame(player)
#                 continue

#             # ------------------------------------------------
#             # FORWARD MOVE OR CHAT TO OPPONENT
#             # ------------------------------------------------

#             # Find this player's room.
#             roomId = player_rooms.get(player)

#             # Ignore messages if player is not in a game.
#             if roomId is None:
#                 continue

#             # Get players in room.
#             players = rooms.get(roomId, [])

#             # Send message to opponent only.
#             for otherPlayer in players.copy():

#                 if otherPlayer != player:
#                     await sendJson(otherPlayer, data)

#     except ConnectionClosed:
#         # Browser disconnected.
#         pass

#     finally:
#         # Always remove player after disconnect.
#         await removePlayerFromGame(player)


# # ============================================================
# # CREATE SUPABASE USERS TABLE
# # ============================================================

# async def createDatabaseTable():

#     # Create table only if it does not exist already.
#     await pool.execute(
#         "CREATE TABLE IF NOT EXISTS users ("
#         "username VARCHAR(20) PRIMARY KEY, "
#         "password_hash TEXT NOT NULL, "
#         "created_at TIMESTAMPTZ DEFAULT NOW()"
#         ")"
#     )


# # ============================================================
# # START SERVER
# # ============================================================

# async def main():

#     global pool

#     # Stop if Render variable is missing.
#     if not databaseUrl:
#         print("DATABASE_URL is missing in Render.")
#         return

#     # Connect Python to Supabase.
#     #
#     # statement_cache_size=0 is needed for Supabase Transaction Pooler.
#     pool = await asyncpg.create_pool(
#         databaseUrl,
#         statement_cache_size=0
#     )

#     # Create users table.
#     await createDatabaseTable()

#     # Render provides PORT automatically.
#     #
#     # 10000 is only used when testing locally.
#     port = int(os.environ.get("PORT", 10000))

#     print(f"Server running on port {port}")

#     # Start WebSocket server.
#     async with websockets.serve(
#         handleClient,
#         "0.0.0.0",
#         port
#     ):
#         # Keep server alive forever.
#         await asyncio.Future()


# # Run main only when Python starts this file directly.
# if __name__ == "__main__":
#     asyncio.run(main())


# # ============================================================
# # DATABASE SETTINGS
# # ============================================================

# # Render stores your Supabase connection string privately.
# #
# # In Render Environment Variables:
# # Key: DATABASE_URL
# # Value: your Supabase Transaction Pooler URL
# databaseUrl = os.environ.get("DATABASE_URL")


# # This becomes the connection pool after the server starts.
# #
# # A pool is a group of reusable database connections.
# pool = None


# # ============================================================
# # ONLINE GAME DATA
# # ============================================================

# # rooms stores every active or waiting room.
# #
# # Example:
# #
# # rooms = {
# #     "abc12345": [whitePlayerSocket, blackPlayerSocket]
# # }
# rooms = {}


# # player_rooms remembers which room each player belongs to.
# #
# # Example:
# #
# # player_rooms[playerSocket] = "abc12345"
# player_rooms = {}


# # A lock prevents two players from joining or creating rooms
# # at the exact same moment and causing a problem.
# room_lock = asyncio.Lock()


# # ============================================================
# # USERNAME RULE
# # ============================================================

# # ^ means "start of text".
# # [A-Za-z0-9_] means letters, numbers, or underscore.
# # {3,20} means between 3 and 20 characters.
# # $ means "end of text".
# USERNAME_RULE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


# # ============================================================
# # PASSWORD FUNCTIONS
# # ============================================================

# def hashPassword(password):
    
#     # Turn a real password into a salted hash.

#     # We NEVER save the real password in Supabase.


#     # Create 16 random bytes.
#     #
#     # This makes each password hash unique, even when two users
#     # choose the same password.
#     salt = secrets.token_bytes(16)

#     # scrypt is a strong password-hashing algorithm.
#     #
#     # password.encode("utf-8") turns text into bytes.
#     passwordHash = hashlib.scrypt(
#         password.encode("utf-8"),
#         salt=salt,
#         n=2**14,
#         r=8,
#         p=1
#     )

#     # Save both the salt and hash as normal text.
#     #
#     # Example format:
#     #
#     # base64Salt$base64PasswordHash
#     return (
#         base64.b64encode(salt).decode("utf-8")
#         + "$"
#         + base64.b64encode(passwordHash).decode("utf-8")
#     )


# def correctPassword(password, savedHash):
#     """
#     Check whether a entered password matches a saved password hash.

#     Returns True if correct.
#     Returns False if wrong or broken.
#     """

#     try:
#         # Split the saved value into:
#         #
#         # saltText
#         # hashText
#         saltText, hashText = savedHash.split("$", 1)

#         # Turn the saved text back into bytes.
#         salt = base64.b64decode(saltText)
#         realHash = base64.b64decode(hashText)

#         # Hash the newly entered password using the old salt.
#         testHash = hashlib.scrypt(
#             password.encode("utf-8"),
#             salt=salt,
#             n=2**14,
#             r=8,
#             p=1
#         )

#         # Compare safely.
#         #
#         # hmac.compare_digest helps prevent timing attacks.
#         return hmac.compare_digest(testHash, realHash)

#     except Exception:
#         # If anything is broken or invalid, treat login as failed.
#         return False


# # ============================================================
# # WEBSOCKET HELPER
# # ============================================================

# async def sendJson(player, data):
#     """
#     Send a Python dictionary to one player as JSON text.
#     """

#     try:
#         # json.dumps changes:
#         #
#         # {"Action": "StartGame"}
#         #
#         # into JSON text that JavaScript can read.
#         await player.send(json.dumps(data))

#     except ConnectionClosed:
#         # Do nothing if the player disconnected while sending.
#         pass


# # ============================================================
# # ACCOUNT CREATION
# # ============================================================

# async def registerPlayer(player, data):
#     """
#     Create a new username/password account.
#     """

#     # Get Username from the message.
#     #
#     # If it does not exist, use "" instead.
#     # str(...) makes sure it is text.
#     # .strip() removes spaces before and after the username.
#     username = str(data.get("Username", "")).strip()

#     # Get Password from the message.
#     password = str(data.get("Password", ""))

#     # Check username format.
#     if not USERNAME_RULE.fullmatch(username):
#         await sendJson(player, {
#             "Action": "RegisterResult",
#             "Success": False,
#             "Message": "Username: use 3-20 letters, numbers, or _ only."
#         })
#         return

#     # Require at least 8 password characters.
#     if len(password) < 8:
#         await sendJson(player, {
#             "Action": "RegisterResult",
#             "Success": False,
#             "Message": "Password must contain at least 8 characters."
#         })
#         return

#     try:
#         # Insert the username and HASHED password into Supabase.
#         #
#         # $1 means the first value after the SQL text.
#         # $2 means the second value after the SQL text.
#         #
#         # asyncpg safely handles values for us.
#         await pool.execute(
#             """
#             INSERT INTO users (username, password_hash)
#             VALUES ($1, $2)
#             """,
#             username,
#             hashPassword(password)
#         )

#         # Tell the browser registration worked.
#         await sendJson(player, {
#             "Action": "RegisterResult",
#             "Success": True,
#             "Message": "Account created. You can log in now."
#         })

#     except asyncpg.UniqueViolationError:
#         # This happens when someone tries to use an existing username.
#         await sendJson(player, {
#             "Action": "RegisterResult",
#             "Success": False,
#             "Message": "That username is already taken."
#         })


# # ============================================================
# # LOGIN
# # ============================================================

# async def loginPlayer(player, data):
#     """
#     Check username and password.

#     Returns the username if login succeeds.
#     Returns None if login fails.
#     """

#     username = str(data.get("Username", "")).strip()
#     password = str(data.get("Password", ""))

#     # Find one matching username in Supabase.
#     user = await pool.fetchrow(
#         """
#         SELECT password_hash
#         FROM users
#         WHERE username = $1
#         """,
#         username
#     )

#     # Login fails if:
#     # - no user was found
#     # - or the password is wrong
#     if user is None or not correctPassword(password, user["password_hash"]):
#         await sendJson(player, {
#             "Action": "LoginResult",
#             "Success": False,
#             "Message": "Wrong username or password."
#         })
#         return None

#     # Tell the browser that login worked.
#     await sendJson(player, {
#         "Action": "LoginResult",
#         "Success": True,
#         "Username": username,
#         "Message": f"Welcome, {username}!"
#     })

#     # Give the username back to handleClient.
#     return username


# # ============================================================
# # MATCHMAKING
# # ============================================================

# async def findGame(player):
#     """
#     Put this logged-in player into a waiting room,
#     or join them to another waiting player.
#     """

#     # Only one player can edit rooms at a time.
#     async with room_lock:

#         # Stop a player from accidentally joining twice.
#         if player in player_rooms:
#             await sendJson(player, {
#                 "Action": "Error",
#                 "Message": "You are already waiting or playing."
#             })
#             return

#         # Look through every existing room.
#         for roomId, players in rooms.items():

#             # A room with one player is waiting for an opponent.
#             if len(players) == 1:

#                 # Add this player as the second player.
#                 players.append(player)

#                 # Remember this player's room.
#                 player_rooms[player] = roomId

#                 # The first player is White.
#                 await sendJson(players[0], {
#                     "Action": "StartGame",
#                     "Turn": 0
#                 })

#                 # The second player is Black.
#                 await sendJson(players[1], {
#                     "Action": "StartGame",
#                     "Turn": 1
#                 })

#                 print(f"Game started in room {roomId}")
#                 return

#         # No waiting room existed, so make one.

#         # Create a short unique room ID.
#         roomId = str(uuid.uuid4())[:8]

#         # Put this player in the new room.
#         rooms[roomId] = [player]

#         # Remember which room they are in.
#         player_rooms[player] = roomId

#         # Tell the player to wait for someone else.
#         await sendJson(player, {
#             "Action": "WaitingForPlayer",
#             "Room": roomId
#         })

#         print(f"Created waiting room {roomId}")


# # ============================================================
# # DISCONNECT / LEAVE GAME
# # ============================================================

# async def removePlayerFromGame(player):
#     """
#     Remove a disconnected player from their room.
#     """

#     async with room_lock:

#         # Find and remove this player's saved room ID.
#         #
#         # If there is no room, roomId becomes None.
#         roomId = player_rooms.pop(player, None)

#         if roomId is None:
#             return

#         # Get the players in that room.
#         players = rooms.get(roomId)

#         # Stop if the room somehow no longer exists.
#         if players is None:
#             return

#         # Remove the disconnected player from the room.
#         if player in players:
#             players.remove(player)

#         # Tell any remaining opponent that the player left.
#         for otherPlayer in players.copy():
#             await sendJson(otherPlayer, {
#                 "Action": "OpponentDisconnected"
#             })

#         # Delete rooms with nobody inside.
#         if len(players) == 0:
#             rooms.pop(roomId, None)

#         print(f"Player left room {roomId}")


# # ============================================================
# # MAIN WEBSOCKET HANDLER
# # ============================================================

# async def handleClient(player):
#     """
#     Runs once for every browser that connects to Render.
#     """

#     # This browser starts with no logged-in account.
#     username = None

#     try:
#         # Keep listening until the player disconnects.
#         async for message in player:

#             try:
#                 # Turn the received JSON text into a Python dictionary.
#                 data = json.loads(message)

#             except json.JSONDecodeError:
#                 # Ignore invalid messages instead of crashing the server.
#                 continue

#             # Read the message action.
#             #
#             # Examples:
#             # Register
#             # Login
#             # FindGame
#             # Move
#             # Chat
#             action = data.get("Action")

#             # ------------------------------------------------
#             # REGISTER
#             # ------------------------------------------------

#             if action == "Register":
#                 await registerPlayer(player, data)
#                 continue

#             # ------------------------------------------------
#             # LOGIN
#             # ------------------------------------------------

#             if action == "Login":
#                 username = await loginPlayer(player, data)
#                 continue

#             # ------------------------------------------------
#             # BLOCK ONLINE PLAY UNTIL LOGIN
#             # ------------------------------------------------

#             if username is None:
#                 await sendJson(player, {
#                     "Action": "Error",
#                     "Message": "Log in before playing online."
#                 })
#                 continue

#             # ------------------------------------------------
#             # FIND A GAME
#             # ------------------------------------------------

#             if action == "FindGame":
#                 await findGame(player)
#                 continue

#             # ------------------------------------------------
#             # FORWARD GAME AND CHAT MESSAGES
#             # ------------------------------------------------

#             # Find this player's room.
#             roomId = player_rooms.get(player)

#             # Ignore messages if they are not in a room.
#             if roomId is None:
#                 continue

#             # Get everyone in that room.
#             players = rooms.get(roomId, [])

#             # Send the message to everyone else in the room.
#             #
#             # This forwards:
#             # - Move
#             # - Chat
#             # - any future game actions
#             for otherPlayer in players.copy():

#                 # Do not send the message back to the sender.
#                 if otherPlayer != player:
#                     await sendJson(otherPlayer, data)

#     except ConnectionClosed:
#         # The player closed the browser, refreshed, or lost connection.
#         pass

#     finally:
#         # This always runs after the player leaves.
#         await removePlayerFromGame(player)


# # ============================================================
# # DATABASE SETUP
# # ============================================================

# async def createDatabaseTables():
#     """
#     Create the users table if it does not exist already.
#     """

#     await pool.execute("""
#         CREATE TABLE IF NOT EXISTS users (
#             username VARCHAR(20) PRIMARY KEY,
#             password_hash TEXT NOT NULL,
#             created_at TIMESTAMPTZ DEFAULT NOW()
#         );
#     """)


# # ============================================================
# # START SERVER
# # ============================================================

# async def main():
#     """
#     Start Supabase, create tables, then start WebSocket server.
#     """

#     global pool

#     # Stop with a clear message if DATABASE_URL is missing in Render.
#     if not databaseUrl:
#         print("DATABASE_URL is missing in Render Environment Variables.")
#         return

#     # Connect to Supabase.
#     #
#     # statement_cache_size=0 is needed for Supabase Transaction Pooler.
#     pool = await asyncpg.create_pool(
#         databaseUrl,
#         statement_cache_size=0
#     )

#     # Create the users table on first start.
#     await createDatabaseTables()

#     # Render provides PORT automatically.
#     #
#     # 10000 is only used if running locally.
#     port = int(os.environ.get("PORT", 10000))

#     print(f"Server running on port {port}")

#     # Start the WebSocket server.
#     #
#     # 0.0.0.0 means Render can access it from outside.
#     async with websockets.serve(
#         handleClient,
#         "0.0.0.0",
#         port
#     ):
#         # Keep the server alive forever.
#         await asyncio.Future()


# # Run main() only when this exact file starts directly.
# if __name__ == "__main__":
#     asyncio.run(main())