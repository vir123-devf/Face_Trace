// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract EvidenceVerifier {
    mapping(bytes32 => bool) public verifiedHashes;
    mapping(bytes32 => uint256) public timestamps;

    event HashSubmitted(bytes32 hash, uint256 timestamp);

    function submitHash(bytes32 hash) public {
        verifiedHashes[hash] = true;
        timestamps[hash] = block.timestamp;
        emit HashSubmitted(hash, block.timestamp);
    }

    function isVerified(bytes32 hash) public view returns (bool) {
        return verifiedHashes[hash];
    }
}
