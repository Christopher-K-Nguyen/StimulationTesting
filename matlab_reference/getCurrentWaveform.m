function [...
    currentAvg,...          	% current (uA)
    time,...                    % time (s)
    vtPlot,...
    buttonHandle]...            % button handle
    = getCurrentWaveform(...
    scope,...                           % oscilloscope
    channelNum,...                      % channel
    amplitude1_new,...                  % present current stimulation
    chargePhase,...                  % charge-per-phase
    chargePhaseLimit,...
    currentMonScale,... % monitor scaling
    numOfRepeats)                       % number of repeats
%% Constants
% Conversion
MILLI_TO_N = 1e-3;	% mV to V
% N_TO_MICRO = 1e6;	% s to us
% N_TO_MILLI = 1e3;	% s to ms
% Waveform check
ROWS = 2;
% MAX_REATTEMPTS = 5;
% TIME_NEG_100 = -100e-6;     % prepulse time to check
% TIME_POS_700 = 700e-6;      % postpulse time to check
% VOLTAGE_THRESH_CHECK = 0.004;   % prepulse voltage (V) threshold
% CURRENT_THRESH_CHECK = 1; 	% prepulse current (uA) threshold

%% Variables
numOfSamples = numOfRepeats + 1;
currentMonScale_V_uA = currentMonScale * MILLI_TO_N;% mV/uA to V/uA
isWaveformNorm = 0;
isCurrentNorm = 0;
attemptNum = 1;

%% Function
% Adjust trigger level
amplitude1_mag = abs(amplitude1_new);
amplitude1_sign = sign(amplitude1_new);
if amplitude1_mag < 10
    triggerLevel = amplitude1_new * currentMonScale_V_uA + 4.5 * currentMonScale_V_uA * amplitude1_sign;
else
    triggerLevel = amplitude1_new * currentMonScale_V_uA * 0.9;
end
triggerLevel_text = sprintf('TRIGger:MAIn:LEVel %f',triggerLevel);
fprintf(scope,'TRIGger:MAIn:EDGe:SOUrce CH2');          % trigger source on current
fprintf(scope,triggerLevel_text);
fprintf('Trigger Level: %.2e A\n',triggerLevel);

while isWaveformNorm == false
    if isCurrentNorm == false
        currentSamples = [];
    end
%     fprintf('Acquiring waveform...\n');
    for sampleNum = 1:numOfSamples
        if chargePhaseLimit ~= 0
            if isCurrentNorm == false
                fprintf('Capturing current...');
                [current] = getWaveform(scope,'CH2',currentMonScale_V_uA,sampleNum,attemptNum);
                if numOfRepeats > 0
                    currentSamples_alloc = [currentSamples,current];
                    currentSamples = currentSamples_alloc;
                    currentAvg = mean(currentSamples,ROWS);
                else
                    currentAvg = current;
                end
                fprintf('OK.\n');
            end
        else
            [currentAvg] = getWaveform2(scope,'CH2',currentMonScale_V_uA);
        end
    end
%     fprintf('Waveform acquired.\n');

    % Acquire time data
    [time] = getTime(scope);

    % Plot
    [vtPlot,buttonHandle] = getAcutePlot(...
        channelNum,...              % channel
        chargePhase,...          % charge-per-phase
        time,...                    % time (s)
        [],...           	% voltage (V)
        currentAvg,...              % current (uA)
        [],...   % time at max cathodal potential
        [],...        % max cathodal potential
        []);          
    
    if ~ishandle(buttonHandle)   	% stop by button handle
        fprintf('Experiment canceled by user...');
        break;
    end
    
    % Check waveform
    isCurrentNorm = 1;
    isWaveformNorm = 1;
end

fprintf(scope,'ACQuire:STAte RUN');

end