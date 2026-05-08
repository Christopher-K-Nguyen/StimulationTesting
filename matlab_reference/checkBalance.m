function isBalanced = checkBalance( ...
    amplitude1,phaseWidth1, ...
    amplitude2,phaseWidth2)

% Check
phase1 = amplitude1 * phaseWidth1;
phase2 = amplitude2 * phaseWidth2;
isBalanced = phase1 + phase2 == 0;

end