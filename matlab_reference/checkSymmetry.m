function isSymmetric = checkSymmetry( ...
    amplitude1,phaseWidth1, ...
    amplitude2,phaseWidth2)

% Sign and magnitude
amplitude1_mag = abs(amplitude1);
amplitude1_sign = sign(amplitude1);
amplitude2_mag = abs(amplitude2);
amplitude2_sign = sign(amplitude2);

% Opposite
isOpposite = amplitude1_sign + amplitude2_sign == 0;

% Check
isAmplitudeSame = amplitude1_mag == amplitude2_mag;
isPhaseWidthSame = phaseWidth1 == phaseWidth2;
isSymmetric = (isAmplitudeSame && isPhaseWidthSame) && isOpposite;

end