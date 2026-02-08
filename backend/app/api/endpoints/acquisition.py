from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import List, Generator
from pydantic import BaseModel
from app.core.device_manager import device_manager
from app.services.picoscan_receiver import PicoscanReceiver, PicoscanReceiverManager
from app.services.point_cloud_processor import PointCloudProcessor
import logging
import json
import time
import asyncio

logger = logging.getLogger(__name__)
router = APIRouter()


class AcquisitionRequest(BaseModel):
    device_ids: List[str]
    num_segments: int = 1


def _extract_segments(result):
    """Normalize scansegmentapi result to a list of segment dicts."""
    if not result:
        return []
    if isinstance(result, tuple):
        segments = result[0]
        return segments if isinstance(segments, list) else [segments]
    return result if isinstance(result, list) else [result]


@router.post("/start-listening")
async def start_listening(request: Request):
    """Start listening for Picoscan UDP data"""
    try:
        # Use app-level receiver manager
        receiver_manager = request.app.state.receiver_manager
        if receiver_manager is None:
            raise Exception('Receiver manager not initialized')

        # Start listeners for devices that are not already present. Do not
        # blindly clear receivers to avoid race conditions and 'address in use'
        # errors when a listener is already bound.
        results = {}
        for device in device_manager.get_all_devices():
            listen_ip = "0.0.0.0"
            # Only start if not already present/listening
            if device.device_id in receiver_manager.receivers:
                results[device.device_id] = True
                continue

            ok = receiver_manager.start_listening(device.device_id, listen_ip, device.port, segments_per_scan=getattr(device, 'segments_per_scan', None))
            results[device.device_id] = ok
        
        return {
            "message": "Started listening for Picoscan UDP data",
            "results": results,
            "info": "Picoscan will send data to port 2115"
        }
    except Exception as e:
        logger.error(f"Error starting: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/stop-listening")
async def stop_listening(request: Request):
    """Stop listening"""
    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    results = receiver_manager.stop_all()
    return {
        "message": "Stopped listening",
        "results": results
    }


@router.post("/receive-data")
async def receive_point_cloud_data(req: AcquisitionRequest, request: Request):
    """
    Receive point cloud data from Picoscan (Compact format)
    
    Picoscan wysyła UDP dane do twojego PC na porcie 2115
    """
    try:
        receiver_manager = request.app.state.receiver_manager
        if receiver_manager is None:
            raise HTTPException(status_code=500, detail="Receiver manager not initialized")

        point_clouds_to_merge = []
        
        for device_id in req.device_ids:
            device = device_manager.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
            
            # Get existing receiver or use temporary one
            receiver_info = receiver_manager.receivers.get(device_id)
            
            if receiver_info and isinstance(receiver_info, dict):
                receiver = receiver_info.get("receiver")
            else:
                receiver = receiver_info
            
            if not receiver or not receiver.connected:
                continue
            
            # Determine segments per scan (device-specific or request)
            segments_per_scan = (
                getattr(device, 'segments_per_scan', None)
                or device_manager.point_cloud_settings.get("segments_per_scan")
                or req.num_segments
                or 1
            )

            # Receive segments
            segments = receiver.receive_segments(segments_per_scan)
            if not segments:
                continue
            
            # Convert to point cloud
            segment_list = _extract_segments(segments)
            points = receiver.segments_to_point_cloud(segment_list)
            
            if len(points) > 0:
                point_clouds_to_merge.append((points, device.calibration))
        
        if not point_clouds_to_merge:
            raise HTTPException(status_code=400, detail="No data received - ensure Picoscan is sending UDP")
        
        merged = PointCloudProcessor.merge_point_clouds(point_clouds_to_merge)
        stats = PointCloudProcessor.calculate_statistics(merged)
        
        return {
            "message": f"Received data from {len(point_clouds_to_merge)} device(s)",
            "total_points": len(merged),
            "devices": req.device_ids,
            "segments_per_device": req.num_segments,
            "statistics": stats
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/receiver-status/{device_id}")
async def get_receiver_status(device_id: str, request: Request):
    """Get status of receiver"""
    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    receiver = receiver_manager.receivers.get(device_id)
    if not receiver:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        return {
            "device_id": device_id,
            "listening": False,
            "info": "Receiver not initialized"
        }
    
    return {
        "device_id": device_id,
        "listening": receiver.connected,
        "info": receiver.get_info()
    }


@router.get("/receiver-metrics/{device_id}")
async def get_receiver_metrics(device_id: str, request: Request):
    """Get receiver metrics (received frames/segments)"""
    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    receiver_info = receiver_manager.receivers.get(device_id)
    if not receiver_info:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        return {
            "device_id": device_id,
            "listening": False,
            "metrics": None,
            "info": "Receiver not initialized"
        }

    receiver = receiver_info.get("receiver") if isinstance(receiver_info, dict) else receiver_info
    metrics = receiver.get_metrics() if hasattr(receiver, "get_metrics") else {}

    return {
        "device_id": device_id,
        "listening": receiver.connected,
        "metrics": metrics
    }


@router.post("/test-receive/{device_id}")
async def test_receive(device_id: str, num_segments: int = 1):
    """Test receiving data from device with point cloud preview"""
    try:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        
        # Create temporary receiver
        receiver = PicoscanReceiver(
            listen_ip="0.0.0.0",
            listen_port=device.port
        )
        
        # Start listening
        if not receiver.start_listening():
            raise HTTPException(
                status_code=400,
                detail=f"Failed to listen on port {device.port}"
            )
        
        # Try to receive data
        segments = receiver.receive_segments(num_segments)
        receiver.stop_listening()
        
        if not segments:
            return {
                "device_id": device_id,
                "status": "listening_but_no_data",
                "message": f"Port {device.port} is open and listening, but no data from Picoscan",
                "info": "Check Picoscan settings - ensure it's configured to send UDP to this PC",
                "points": []
            }
        
        # Convert to point cloud
        segment_list = _extract_segments(segments)
        points = receiver.segments_to_point_cloud(segment_list)
        
        # Convert numpy array to list for JSON serialization
        if hasattr(points, 'tolist'):
            points_list = points.tolist()
        else:
            points_list = list(points)
        
        return {
            "device_id": device_id,
            "status": "success",
            "points_received": len(points_list),
            "port": device.port,
            "points": points_list
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Test failed: {str(e)}")


@router.get("/live-preview/{device_id}")
async def live_preview(device_id: str, request: Request):
    """Stream live point cloud data from device using Server-Sent Events"""
    try:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        
        # Get receiver manager from app state
        receiver_manager = request.app.state.receiver_manager
        if not receiver_manager:
            raise HTTPException(status_code=500, detail="Receiver manager not initialized")
        
        async def event_generator():
            """Generate SSE events with point cloud data"""
            # Check if receiver already exists (from auto-start)
            receiver_info = receiver_manager.receivers.get(device_id)
            
            if not receiver_info:
                # No active receiver - create temporary one
                receiver = PicoscanReceiver(
                    listen_ip="0.0.0.0",
                    listen_port=device.port
                )
                
                # Try to start listening
                if not receiver.start_listening():
                    yield f"data: {json.dumps({'error': 'Failed to start listening on port ' + str(device.port)})}\n\n"
                    return
                
                own_receiver = True
            else:
                # Use existing receiver from auto-start
                if isinstance(receiver_info, dict) and "receiver" in receiver_info:
                    receiver = receiver_info["receiver"]
                else:
                    receiver = receiver_info
                own_receiver = False
            
            try:
                logger.info(f"Live preview started for {device_id}")
                
                # Stream data continuously
                frame_count = 0
                while True:
                    try:
                        # Receive one segment at a time
                        # Determine segments per scan (device-specific or default)
                        segments_per_scan = None
                        try:
                            device = device_manager.get_device(device_id)
                            segments_per_scan = (
                                getattr(device, 'segments_per_scan', None)
                                or device_manager.point_cloud_settings.get("segments_per_scan")
                            )
                        except Exception:
                            segments_per_scan = None

                        segments_to_get = segments_per_scan or 1
                        segments = receiver.receive_segments(num_segments=segments_to_get)
                        if not segments:
                            # Heartbeat to keep SSE alive, but do not clear points on client
                            yield f"data: {json.dumps({'status': 'waiting', 'device_id': device_id})}\n\n"
                            await asyncio.sleep(0.05)
                            continue

                        segment_list = _extract_segments(segments)
                        points = receiver.segments_to_point_cloud(segment_list)
                        
                        if hasattr(points, 'tolist'):
                            points_list = points.tolist()
                        else:
                            points_list = list(points)

                        # Prefer frame number from segment if available
                        frame_number = None
                        try:
                            if segment_list and segment_list[0].get("Modules"):
                                frame_number = segment_list[0]["Modules"][0].get("FrameNumber")
                        except Exception:
                            frame_number = None

                        frame_count += 1
                        data = {
                            "frame": frame_number if frame_number is not None else frame_count,
                            "points": points_list,
                            "count": len(points_list),
                            "device_id": device_id,
                            "status": "streaming"
                        }
                        
                        yield f"data: {json.dumps(data)}\n\n"

                        # Small non-blocking delay to prevent CPU spinning
                        await asyncio.sleep(0.01)
                        
                    except Exception as e:
                        logger.debug(f"Error in streaming loop: {e}")
                        yield f"data: {json.dumps({'error': str(e)})}\n\n"
                        await asyncio.sleep(0.5)
                    except asyncio.CancelledError:
                        # Generator was cancelled (server shutdown / client disconnect)
                        break

                
            
            except Exception as e:
                logger.error(f"Live preview error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            
            finally:
                # Only close if this endpoint created the receiver
                if own_receiver:
                    try:
                        receiver.stop_listening()
                    except:
                        pass
        
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
