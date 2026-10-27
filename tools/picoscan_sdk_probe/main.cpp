// Read-only diagnostic receiver. Never configures the sensor or publishes measurements.
#ifdef _WIN32
#define NOMINMAX
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#endif
#include <sick_perception_sdk/compact_format/telegram_type_1_scan_data/ScanDataParser.hpp>
#include <sick_perception_sdk/compact_format/telegram_type_1_scan_data/PointCloudConverter.hpp>
#include <chrono>
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <set>
#include <map>
#include <vector>
#include <string>
#include <stdexcept>

int main(int argc, char** argv) {
  try {
    if (argc != 6) {
      std::cerr << "Usage: picoscan_sdk_probe BIND_IP SENSOR_IP PORT SECONDS OUTPUT.csv\n";
      return 2;
    }
    const int port = std::stoi(argv[3]), seconds = std::stoi(argv[4]);
    if (port < 1 || port > 65535 || seconds < 1 || seconds > 3600) throw std::runtime_error("Invalid port/duration");
#ifdef _WIN32
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2,2), &wsa)) throw std::runtime_error("WSAStartup failed");
    auto sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock == INVALID_SOCKET) throw std::runtime_error("socket failed");
    BOOL exclusive = TRUE;
    setsockopt(sock, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<char*>(&exclusive), sizeof(exclusive));
#else
    auto sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) throw std::runtime_error("socket failed");
#endif
    sockaddr_in local{}, sensor{};
    local.sin_family = AF_INET; local.sin_port = htons(static_cast<unsigned short>(port));
    if (inet_pton(AF_INET, argv[1], &local.sin_addr) != 1 || inet_pton(AF_INET, argv[2], &sensor.sin_addr) != 1)
      throw std::runtime_error("Invalid IPv4 address");
    if (bind(sock, reinterpret_cast<sockaddr*>(&local), sizeof(local))) throw std::runtime_error("bind failed: address unavailable or port occupied");
    int bufferSize = 4 * 1024 * 1024;
    setsockopt(sock, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<char*>(&bufferSize), sizeof(bufferSize));
#ifdef _WIN32
    int optlen = sizeof(bufferSize);
#else
    socklen_t optlen = sizeof(bufferSize);
#endif
    if (getsockopt(sock, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<char*>(&bufferSize), &optlen))
      throw std::runtime_error("Cannot read actual socket receive buffer size");
    std::ofstream csv(argv[5]);
    if (!csv) throw std::runtime_error("Cannot open output CSV");
    csv << "host_elapsed_s,frame,segment,telegram,modules,distance_samples,positive_samples,sdk_points,first_beam_us,last_beam_us,parse_ms\n";
    sick::compact::scan_data::PointCloudConverter converter;
    std::vector<unsigned char> bytes(65536);
    std::map<std::size_t, std::set<std::size_t>> frames;
    std::size_t packets=0, errors=0, ignored=0, duplicates=0, points=0;
    const auto start = std::chrono::steady_clock::now();
    std::cout << "Listening " << argv[1] << ':' << port << ", sensor=" << argv[2] << ", SO_RCVBUF=" << bufferSize << "\n";
    while (std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count() < seconds) {
      fd_set reads; FD_ZERO(&reads); FD_SET(sock, &reads);
      timeval wait{0,200000};
      const int ready = select(static_cast<int>(sock)+1, &reads, nullptr, nullptr, &wait);
      if (ready < 0) throw std::runtime_error("select failed");
      if (!ready) continue;
      sockaddr_in from{};
#ifdef _WIN32
      int fromlen = sizeof(from);
#else
      socklen_t fromlen = sizeof(from);
#endif
      const int size = recvfrom(sock, reinterpret_cast<char*>(bytes.data()), static_cast<int>(bytes.size()), 0,
                                reinterpret_cast<sockaddr*>(&from), &fromlen);
      if (size < 0) throw std::runtime_error("recvfrom failed");
      const auto receipt = std::chrono::steady_clock::now();
      if (from.sin_addr.s_addr != sensor.sin_addr.s_addr) { ++ignored; continue; }
      ++packets;
      try {
        const auto data = sick::compact::scan_data::Parser::validateAndParse(sick::ByteView(bytes.data(), size), true);
        const auto cloud = converter.convert(data);
        std::size_t samples=0, positive=0;
        uint64_t first=UINT64_MAX, last=0;
        for (const auto& module : data.modules) {
          samples += module.distances.size();
          for (const auto& distance : module.distances) if (distance.meters() > 0) ++positive;
          for (const auto& row : module.rowMetaData) {
            first=std::min(first,row.firstBeamTimestamp.microsecondsSinceEpoch());
            last=std::max(last,row.lastBeamTimestamp.microsecondsSinceEpoch());
          }
        }
        if (!frames[data.frameSequenceNumber].insert(data.segmentIndex).second) ++duplicates;
        points += cloud.numberOfPoints();
        csv << std::chrono::duration<double>(receipt-start).count() << ',' << data.frameSequenceNumber << ','
            << data.segmentIndex << ',' << data.telegramHeader.telegramSequenceNumber << ',' << data.modules.size() << ','
            << samples << ',' << positive << ',' << cloud.numberOfPoints() << ',' << (first==UINT64_MAX?0:first) << ',' << last << ','
            << std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-receipt).count() << '\n';
      } catch (const std::exception& e) {
        ++errors;
        if (errors <= 10) std::cerr << "Parse/conversion error: " << e.what() << " bytes=" << size << '\n';
      }
    }
    std::map<std::set<std::size_t>,std::size_t> patterns;
    for (const auto& frame : frames) ++patterns[frame.second];
    std::cout << "datagrams=" << packets << " errors=" << errors << " ignored_sources=" << ignored
              << " observed_frames=" << frames.size() << " duplicate_segments=" << duplicates << " sdk_points=" << points << '\n';
    for (const auto& pattern : patterns) {
      std::cout << "segment_ids=";
      for (const auto id : pattern.first) std::cout << id << ';';
      std::cout << " frames=" << pattern.second << '\n';
    }
    std::cout << "Observed patterns are NOT proof of completeness. First/last frames may be partial.\n";
    csv.flush();
    if (!csv) throw std::runtime_error("CSV write failed (disk full or unavailable)");
#ifdef _WIN32
    closesocket(sock); WSACleanup();
#else
    close(sock);
#endif
    return packets == 0 ? 3 : (errors ? 4 : 0);
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n'; return 1;
  }
}
