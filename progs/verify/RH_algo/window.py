"""
PRAC: Pattern Recognition and Compression
Analyzes trace files and replaces repeating patterns with REF commands.
"""

import sys
import argparse
from typing import List, Dict, Tuple, Optional
from collections import defaultdict


class PatternRecognition:
    """
    Detects and compresses repeating patterns in trace files.
    """
    
    def __init__(self, window_size: int = 10, threshold: float = 0.8):
        """
        Args:
            window_size: Size of the sliding window for pattern matching
            threshold: Similarity threshold (0.0-1.0) for pattern matching
        """
        self.window_size = window_size
        self.threshold = threshold
        self.patterns = {}  # Maps pattern signature to pattern ID
        self.pattern_list = []  # List of patterns in order of discovery
        self.next_pattern_id = 0
    
    def _normalize_command(self, cmd: str) -> str:
        """Normalize a command for comparison (e.g., replace numeric values with placeholders)."""
        parts = cmd.split()
        if not parts:
            return cmd
        
        cmd_type = parts[0]
        
        # For numeric addresses, normalize them
        normalized_parts = [cmd_type]
        for i, part in enumerate(parts[1:], 1):
            # Keep register names and special identifiers, replace numbers
            if part in ['B_DCC1', 'B_DCC1N', 'C0', 'T0', 'T1', 'T2', 'T3']:
                normalized_parts.append(part)
            elif part.isdigit():
                normalized_parts.append('$NUM')
            else:
                normalized_parts.append(part)
        
        return ' '.join(normalized_parts)
    
    def _calculate_pattern_similarity(self, pattern1: List[str], pattern2: List[str]) -> float:
        """Calculate similarity between two patterns (0.0 to 1.0)."""
        if len(pattern1) != len(pattern2):
            return 0.0
        
        if len(pattern1) == 0:
            return 1.0
        
        matches = 0
        for cmd1, cmd2 in zip(pattern1, pattern2):
            norm1 = self._normalize_command(cmd1)
            norm2 = self._normalize_command(cmd2)
            if norm1 == norm2:
                matches += 1
        
        return matches / len(pattern1)
    
    def _find_similar_pattern(self, pattern: List[str]) -> Optional[int]:
        """Find if a similar pattern already exists."""
        for existing_id, existing_pattern in enumerate(self.pattern_list):
            similarity = self._calculate_pattern_similarity(pattern, existing_pattern)
            if similarity >= self.threshold:
                return existing_id
        return None
    
    def _extract_numeric_values(self, commands: List[str]) -> List[int]:
        """Extract numeric addresses/values from commands for REF output."""
        numbers = []
        for cmd in commands:
            parts = cmd.split()
            for part in parts:
                if part.isdigit():
                    numbers.append(int(part))
        
        # Deduplicate and sort
        unique_numbers = sorted(set(numbers))
        return unique_numbers[:10]  # Limit to 10 numbers for compact representation
    
    def parse_trace(self, filename: str) -> List[str]:
        """Parse a trace file."""
        with open(filename, 'r') as f:
            lines = f.readlines()
        
        # First line contains row addresses
        self.row_addresses = lines[0].strip()
        
        # Remaining lines contain commands
        commands = []
        for line in lines[1:]:
            line = line.strip()
            if line:
                commands.append(line)
        
        return commands
    
    def compress(self, commands: List[str]) -> List[str]:
        """
        Compress commands by finding and replacing patterns with REF commands.
        
        Args:
            commands: List of command strings
            
        Returns:
            List of compressed commands with REF replacements
        """
        compressed = []
        i = 0
        
        while i < len(commands):
            # Try to match a pattern starting at position i
            matched = False
            
            # Try different pattern lengths from window_size down to 2
            for pattern_len in range(min(self.window_size, len(commands) - i), 1, -1):
                if i + pattern_len > len(commands):
                    continue
                
                # Extract candidate pattern
                candidate_pattern = commands[i:i + pattern_len]
                
                # Check if similar pattern exists
                similar_id = self._find_similar_pattern(candidate_pattern)
                
                if similar_id is not None:
                    # Found a match - create REF command
                    numeric_values = self._extract_numeric_values(candidate_pattern)
                    ref_cmd = self._create_ref_command(similar_id, numeric_values)
                    compressed.append(ref_cmd)
                    i += pattern_len
                    matched = True
                    break
                else:
                    # No match found, register this as a new pattern
                    if pattern_len == self.window_size:
                        self.pattern_list.append(candidate_pattern)
                        self.next_pattern_id += 1
            
            if not matched:
                # No pattern matched, keep the command as-is
                compressed.append(commands[i])
                i += 1
        
        return compressed
    
    def _create_ref_command(self, pattern_id: int, numeric_values: List[int]) -> str:
        """Create a REF command string."""
        # Format: REF <pattern_id> <numeric_values...> T0
        ref_parts = ['REF', str(pattern_id)]
        ref_parts.extend(str(v) for v in numeric_values)
        ref_parts.append('T0')
        return ' '.join(ref_parts)
    
    def write_trace(self, commands: List[str], output_filename: str):
        """Write compressed trace to output file."""
        with open(output_filename, 'w') as f:
            f.write(self.row_addresses + '\n')
            for cmd in commands:
                f.write(cmd + '\n')
    
    def process_file(self, input_filename: str, output_filename: str) -> Dict:
        """
        Process a trace file end-to-end.
        
        Args:
            input_filename: Input trace file path
            output_filename: Output trace file path
            
        Returns:
            Dictionary with compression statistics
        """
        # Parse input
        commands = self.parse_trace(input_filename)
        original_count = len(commands)
        
        # Compress
        compressed_commands = self.compress(commands)
        compressed_count = len(compressed_commands)
        
        # Write output
        self.write_trace(compressed_commands, output_filename)
        
        # Return statistics
        return {
            'original_commands': original_count,
            'compressed_commands': compressed_count,
            'compression_ratio': compressed_count / original_count if original_count > 0 else 0,
            'unique_patterns': len(self.pattern_list),
            'reduction': original_count - compressed_count
        }


def main():
    parser = argparse.ArgumentParser(
        description='PRAC: Pattern Recognition and Compression for trace files'
    )
    parser.add_argument('input_file', help='Input trace file')
    parser.add_argument('-o', '--output', help='Output trace file', 
                       default=None)
    parser.add_argument('-w', '--window-size', type=int, default=10,
                       help='Sliding window size for pattern matching (default: 10)')
    parser.add_argument('-t', '--threshold', type=float, default=0.8,
                       help='Similarity threshold (0.0-1.0) for pattern matching (default: 0.8)')
    parser.add_argument('--stats', action='store_true',
                       help='Print compression statistics')
    
    args = parser.parse_args()
    
    # Determine output filename
    if args.output is None:
        base = args.input_file.rsplit('.', 1)[0] if '.' in args.input_file else args.input_file
        args.output = f"{base}_prac.txt"
    
    # Run compression
    prac = PatternRecognition(window_size=args.window_size, threshold=args.threshold)
    stats = prac.process_file(args.input_file, args.output)
    
    print(f"Processed: {args.input_file}")
    print(f"Output: {args.output}")
    
    if args.stats:
        print("\n=== Compression Statistics ===")
        print(f"Original commands: {stats['original_commands']}")
        print(f"Compressed commands: {stats['compressed_commands']}")
        print(f"Commands reduced: {stats['reduction']}")
        print(f"Compression ratio: {stats['compression_ratio']:.4f}")
        print(f"Unique patterns found: {stats['unique_patterns']}")


if __name__ == '__main__':
    main()
