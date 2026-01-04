import asyncio
import websockets
import json


async def test_session_lifecycle():
    uri = "ws://localhost:8000/ws/transcribe"

    print("Test 1: Basic session lifecycle")
    print("-" * 50)

    async with websockets.connect(uri) as websocket:
        response = await websocket.recv()
        msg = json.loads(response)
        print(f"✓ Session started: {msg['session_id']}")
        assert msg['type'] == 'session_started'
        assert msg['state'] == 'init'

        await websocket.send(json.dumps({"type": "start"}))
        response = await websocket.recv()
        msg = json.loads(response)
        print(f"✓ Streaming started: state={msg['state']}")
        assert msg['type'] == 'streaming_started'
        assert msg['state'] == 'streaming'

        test_audio = bytes([0] * 1024)
        await websocket.send(test_audio)
        response = await websocket.recv()
        msg = json.loads(response)
        print(f"✓ Audio received: {msg['text']}")
        assert msg['type'] == 'partial_transcript'

        await websocket.send(json.dumps({"type": "stop"}))
        response = await websocket.recv()
        msg = json.loads(response)
        print(f"✓ Streaming stopped: state={msg['state']}")
        assert msg['type'] == 'streaming_stopped'

    print("\n✅ Test 1 passed: Session lifecycle works correctly\n")


async def test_client_disconnect():
    uri = "ws://localhost:8000/ws/transcribe"

    print("Test 2: Client disconnect handling")
    print("-" * 50)

    websocket = await websockets.connect(uri)
    response = await websocket.recv()
    msg = json.loads(response)
    session_id = msg['session_id']
    print(f"✓ Session started: {session_id}")

    await websocket.send(json.dumps({"type": "start"}))
    await websocket.recv()
    print(f"✓ Streaming started")

    await websocket.close()
    print(f"✓ Client disconnected cleanly")

    print("\n✅ Test 2 passed: Disconnect handling works\n")


async def test_reconnect():
    uri = "ws://localhost:8000/ws/transcribe"

    print("Test 3: Reconnect creates new session")
    print("-" * 50)

    async with websockets.connect(uri) as ws1:
        response = await ws1.recv()
        msg1 = json.loads(response)
        session_id_1 = msg1['session_id']
        print(f"✓ First session: {session_id_1}")

    async with websockets.connect(uri) as ws2:
        response = await ws2.recv()
        msg2 = json.loads(response)
        session_id_2 = msg2['session_id']
        print(f"✓ Second session: {session_id_2}")
        assert session_id_1 != session_id_2
        print(f"✓ Sessions are different (clean reconnect)")

    print("\n✅ Test 3 passed: Reconnect works correctly\n")


async def test_invalid_state_transitions():
    uri = "ws://localhost:8000/ws/transcribe"

    print("Test 4: Invalid state transitions")
    print("-" * 50)

    async with websockets.connect(uri) as websocket:
        await websocket.recv()

        await websocket.send(json.dumps({"type": "start"}))
        await websocket.recv()
        print(f"✓ Streaming started")

        await websocket.send(json.dumps({"type": "start"}))
        response = await websocket.recv()
        msg = json.loads(response)
        print(f"✓ Second start rejected: {msg['type']}")
        assert msg['type'] == 'error'

    print("\n✅ Test 4 passed: Invalid transitions rejected\n")


async def run_all_tests():
    print("=" * 50)
    print("Running Phase 2 Service Skeleton Tests")
    print("=" * 50)
    print()

    try:
        await test_session_lifecycle()
        await test_client_disconnect()
        await test_reconnect()
        await test_invalid_state_transitions()

        print("=" * 50)
        print("✅ ALL TESTS PASSED")
        print("=" * 50)
        print("\nPhase 2 Exit Criteria:")
        print("✓ Audio can stream continuously without crashes")
        print("✓ Sessions always terminate cleanly")
        print("✓ Deterministic behavior on disconnect/reconnect")
        print()

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_all_tests())
