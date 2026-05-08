function [currentStim_new,currentChange] = adjustCurrentStim2(...
    currentStim,...
    chargePhaseLimit,...
    stepSize,...
    potentialLimit,...
    potentialExcursion,...
    surfaceArea,...
    rateTest)
%% Constants
CURRENT_COMPLIANCE = 1e3;
CURRENT_MIN = 0.1;
% Max potential and lower potential limit difference
MAX_POTENTIAL_DIFF_THRESH = [...
    600e-3,...
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
    50,...
    40,...
    30,...
    20,...
    10,...
    4,...
    2,...
    1,...
    0.5];

% Current decrement (uA)
CURRENT_DECREMENT = -[...
    20,...
    15,...
    10,...
    6,...
    3.2,...
    2.5,...
    1.4,...
    0.6,...
    0.3,...
    0.2];

%% Variable
currentStim_new = currentStim;
scale = 1;
if surfaceArea >= 1000 && surfaceArea <= 2000
    scale = 1;
end
if surfaceArea >= 2000
    scale = 2;
end
if surfaceArea >= 50 && surfaceArea <= 200
    scale = 0.2;
end
if surfaceArea > 200 && surfaceArea < 1000
    scale = 0.5;
end
if ~isempty(rateTest)
    isPostAcute = rateTest(1);
    dutyCycle = rateTest(2);
    if isPostAcute
        if dutyCycle >= 0.3
            scale = scale * 0.25;
        elseif dutyCycle >= 0.2
            scale = scale * 0.5;
        elseif dutyCycle >= 0.1
            scale = scale * 1;
        else
            scale = scale * 2;
        end
    end
end
currentInc = CURRENT_INCREMENT * scale;
currentDec = CURRENT_DECREMENT * scale;

%% Function
fprintf('\n');
% Distance to lower limit
potentialExcursion_diff = potentialExcursion - potentialLimit;
potentialExcursion_diff_mag = abs(potentialExcursion_diff);
% fprintf('Voltage difference from potential limit: %.3f V\n',potentialExcursionDiff_mag);
if potentialLimit < 0
    limitPotential_sign = -1;
    if potentialExcursion > potentialLimit
        potentialExcursion_diff_sign = 1;
    else
        potentialExcursion_diff_sign = -1;
    end
else
    limitPotential_sign = 1;
    if potentialExcursion < potentialLimit
        potentialExcursion_diff_sign = 1;
    else
        potentialExcursion_diff_sign = -1;
    end
end
% fprintf('Max Potential sign: %d\n',potentialExcursionDiff_sign);
% potentialExcursionDiff_sign = sign(potentialExcursionDiff);
switch potentialExcursion_diff_sign
   case 1
          fprintf('Potential difference ABOVE target: %0.3f V\n',potentialExcursion_diff_mag);
   case -1
       fprintf('Potential difference BELOW target: %0.3f V\n',potentialExcursion_diff_mag);
end
if chargePhaseLimit == 0 || isinf(chargePhaseLimit)   % stimulating to max cathodal limit
%     fprintf('Potential difference: %0.3f V\n',potentialExcursion_diff_mag);
    fprintf('Adjusting current amplitude %.1f uA...',currentStim);
    if potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(1)     % above 500 mV
        idx = 1;
    elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(2) % above 400 mV
        idx = 2;
    elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(3) % above 200 mV
        idx = 3;
    elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(4) % above 100 mV
        idx = 4;
    elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(5) % above 50 mV
        idx = 5;
    elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(6) % above 10 mV
        idx = 6;
    elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(7) % above 5 mV
        idx = 7;
    elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(8) % above 1 mV
        idx = 8;
    elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(9) % above 1 mV
        idx = 9;
    end
    switch potentialExcursion_diff_sign
        case 1 % above potential
            fprintf('+');
            currentChange = currentInc(idx);
        case -1 % below potential
            fprintf('-');
            currentChange = currentDec(idx);   
    end
    currentChange = round(currentChange,1);
    if currentChange == 0
        currentChange = CURRENT_MIN * limitPotential_sign;
    end
%     if potentialExcursion_diff_mag > 1
%         currentChange = currentChange / 2;
%     end
    currentStim_new = currentStim + currentChange;
    if currentStim_new > CURRENT_COMPLIANCE
        currentStim_new = CURRENT_COMPLIANCE;
        currentChange = currentStim_new - currentStim;
    end
    while currentStim_new <= 0
        currentChange = round(currentChange * 0.9,1);
        currentStim_new = currentStim + currentChange;
        if abs(currentChange) < CURRENT_MIN
            currentChange = CURRENT_MIN * limitPotential_sign;
            currentStim_new = currentStim + currentChange;
            break;
        end
    end
    fprintf('%g uA...',abs(currentChange));

else % stimulating to charge-per-phase target
    fprintf('Adjusting current amplitude %.1f uA...',currentStim);
    fprintf('+%.1f uA...',stepSize);
    currentStim_new = currentStim_new + stepSize;   % increment current by step size
    currentChange = stepSize;
end
currentStim_new = abs(currentStim_new);
fprintf('%.1f uA\n',currentStim_new);
% potentialExcursionTresh = MAX_POTENTIAL_DIFF_THRESH(idx);
% fprintf('Voltage difference threshold: %.3f V',potentialExcursionTresh);

end