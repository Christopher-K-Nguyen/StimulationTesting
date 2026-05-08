function [currentStim_new] = adjustCurrentStim(...
    geomSurfaceArea,...
    currentStim,...
    stepSize,...
    limitPotential,...
    maxPotential,...
    chargePhaseLimit)
%% Constants
% Max potential and lower potential limit difference
MAX_POTENTIAL_DIFF_THRESH = [...
    500e-3,...
    400e-3,...
    200e-3,...
    100e-3,...
    50e-3,...
    20e-3,...
    10e-3,...
    5e-3,...
    1e-3];
% Current increment (uA)
CURRENT_INCREMENT = [...
    60,...
    40,...
    20,...
    8,...
    4,...
    2,...
    1,...
    0.5,...
    0.2];
% Current decrement (uA)
CURRENT_DECREMENT = -[...
    50,...
    30,...
    15,...
    9,...
    7,...
    5,...
    3,...
    2,...
    0.5];

%% Variable
currentStim_new = currentStim;
scale = 1;
if geomSurfaceArea >= 1000 && geomSurfaceArea <= 2000
    scale = 1;
end
if geomSurfaceArea >= 2000
    scale = 2;
end
if geomSurfaceArea >= 50 && geomSurfaceArea <= 200
    scale = 0.2;
end
if geomSurfaceArea > 200 && geomSurfaceArea < 1000
    scale = 0.5;
end
currentInc = CURRENT_INCREMENT * scale;
currentDec = CURRENT_DECREMENT * scale;
%% Function
% Distance to lower limit
maxPotentialDiff = maxPotential - limitPotential;
maxPotentialDiff_mag = abs(maxPotentialDiff);
% fprintf('Voltage difference from potential limit: %.3f V\n',maxPotentialDiff_mag);
if limitPotential < 0
    if maxPotential > limitPotential
        maxPotentialDiff_sign = 1;
    else
        maxPotentialDiff_sign = -1;
    end
else
    if maxPotential < limitPotential
        maxPotentialDiff_sign = 1;
    else
        maxPotentialDiff_sign = -1;
    end
end
% fprintf('Max Potential sign: %d\n',maxPotentialDiff_sign);
% maxPotentialDiff_sign = sign(maxPotentialDiff);
fprintf('\n');
if chargePhaseLimit == 0 || isinf(chargePhaseLimit)   % stimulating to max cathodal limit
    fprintf('Adjusting current amplitude %g uA...',currentStim);
    switch maxPotentialDiff_sign
        case 1 % above potential
            fprintf('incrementing ');
            if maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(1)     % above 500 mV
                idx = 1;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(2) % above 400 mV
                idx = 2;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(3) % above 200 mV
                idx = 3;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(4) % above 100 mV
                idx = 4;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(5) % above 50 mV
                idx = 5;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(6) % above 10 mV
                idx = 6;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(7) % above 5 mV
                idx = 7;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(8) % above 1 mV
                idx = 8;
            end
            currentChange = currentInc(idx);
        case -1 % below potential
            fprintf('decrementing ');
            if maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(1)     % below 500 mV
                idx = 1;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(2) % below 400 mV
                idx = 2;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(3) % below 200 mV
                idx = 3;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(4) % below 100 mV
                idx = 4;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(5) % below 50 mV
                idx = 5;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(6) % below 10 mV
                idx = 6;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(7) % above 5 mV
                idx = 7;
            elseif maxPotentialDiff_mag > MAX_POTENTIAL_DIFF_THRESH(8) % below 1 mV
                idx = 8;
            end
            currentChange = currentDec(idx);   
    end
    if maxPotentialDiff_mag > 1
        currentChange = currentChange / 2;
    end
    currentStim_new = currentStim + currentChange;
    while currentStim_new <= 0
        currentChange = currentChange / 2;
        currentStim_new = currentStim + currentChange;
    end
    fprintf('%g uA...',currentChange);

else % stimulating to charge-per-phase target
    fprintf('Adjusting current amplitude %g uA...',currentStim);
    fprintf('incrementing %g uA...',stepSize);
    currentStim_new = currentStim_new + stepSize;   % increment current by step size
end
currentStim_new = abs(currentStim_new);
fprintf('%g uA\n',currentStim_new);
% maxPotentialTresh = MAX_POTENTIAL_DIFF_THRESH(idx);
% fprintf('Voltage difference threshold: %.3f V',maxPotentialTresh);

end