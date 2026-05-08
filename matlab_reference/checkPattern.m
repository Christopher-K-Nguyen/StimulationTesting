function [isBalanced,isSymmetric] = checkPattern( ...
    amplitude1,phaseWidth1, ...
    amplitude2,phaseWidth2, ...
    varargin)

if ~isempty(varargin)
    amplitude3 = varargin{1};
    phaseWidth3 = varargin{2};
end

% Sign and magnitude
amplitude1_sign = sign(amplitude1);	% sign of first phase amplitude
amplitude1_mag = abs(amplitude1);   % magnitude of first phase amplitude
amplitude2_sign = sign(amplitude2);	% sign of second phase amplitude
amplitude2_mag = abs(amplitude2);   % magnitude of second phase amplitude
if ~isempty(varargin)
    amplitude3_sign = sign(amplitude3);	% sign of second phase amplitude
    amplitude3_mag = abs(amplitude3);   % magnitude of second phase amplitude
end

% Number of Widths
numOfPhaseWidth1 = length(phaseWidth1);
numOfPhaseWidth2 = length(phaseWidth2);

% Opposite
if ~isempty(varargin)
    isOpposite = all(amplitude1_sign * amplitude2_sign * amplitude3_sign == -1);
else
    isOpposite = all(amplitude1_sign * amplitude2_sign == -1);
end

% Symmetry
if ~isempty(varargin)
    isAmplitudeSame = amplitude1_mag + amplitude3_mag == amplitude2_mag;
    isPhaseWidthSame = isequal(phaseWidth1,phaseWidth2,phaseWidth3);
    isSymmetric = (isAmplitudeSame && isPhaseWidthSame) && isOpposite;
else
    isAmplitudeSame = amplitude1_mag == amplitude2_mag;
    isPhaseWidthSame = phaseWidth1 == phaseWidth2;
    if (numOfPhaseWidth1 > 1 || numOfPhaseWidth2 > 1) || ...
        numOfPhaseWidth1 ~= numOfPhaseWidth2
        isSymmetric = false;
    else
        isSymmetric = (isAmplitudeSame && isPhaseWidthSame) && isOpposite;
    end
end

% Charge Balance
if ~isempty(varargin)
    phase1 = amplitude1 * phaseWidth1;
    phase2 = amplitude2 * phaseWidth2;
    phase3 = amplitude3 * phaseWidth3;
    isBalanced = phase1 + phase2 + phase3 == 0;
else
    phase1 = amplitude1 * phaseWidth1;
    phase2 = amplitude2 * phaseWidth2;
    isBalanced = phase1 + phase2 == 0;
end

end