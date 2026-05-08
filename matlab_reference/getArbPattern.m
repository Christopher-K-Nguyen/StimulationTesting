function filePath_cell = getArbPattern(File,pattern,varargin)
%% Variables
% Channel
if ~isempty(varargin)
    channel_arr = varargin{1};
else
    channel_arr = [];
end
numOfChannels = length(channel_arr);
if numOfChannels == 0
    numOfFiles = 1;
else
    numOfFiles = numOfChannels;
end
% capture_arr = [File.Data(groupNum).Capture(:).Index];
% numOfCaptures = length(capture_arr);

% Experiment
expType = File.Test.Experiment;
isTriphasic = contains2(expType,'TV');

% Pattern
if isempty(pattern)
    filePath_cell = {};
    return;
end
amplitude1 = pattern.A1 * 1e3;
amplitude2 = pattern.A2 * 1e3;
phaseWidth1 = round(pattern.W1);
phaseWidth2 = round(pattern.W2);
interphaseDelay = round(pattern.Delay);
hasInterphaseDelay = interphaseDelay > 0;
if isTriphasic
    amplitude3 = pattern.A3 * 1e3;
    amplitude3_sign = sign(amplitude3);
    amplitude3_fix = amplitude3_sign * round(abs(amplitude3));
    phaseWidth3 = round(pattern.W3);
end
depolTime = round(File.Parameters.Depolarization);
dischargeDelay = round(File.Parameters.DischargeDelay);
hasDischargeDelay = dischargeDelay > 0;
stimRate = File.Parameters.StimulationRate;
zero_current = 0;

% Pulse
pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
if isTriphasic
    pulseWidth = pulseWidth + interphaseDelay + phaseWidth3;
end
testWidth = pulseWidth + dischargeDelay;

% Time before discharge
if hasDischargeDelay
    stimPeriod = 1 / stimRate * 1e6;
    interpulseDelay = stimPeriod - pulseWidth;
    if dischargeDelay > interpulseDelay
        while dischargeDelay > interpulseDelay
            dischargeDelay = round(dischargeDelay / factor);
            if dischargeDelay < depolTime
                dischargeDelay = depolTime;
                break;
            end
        end
    end
    File.Parameters.DischargeDelay = dischargeDelay;
end

%% Write to Pattern File
if numOfFiles > 1
    fprintf('Generating arbitrary...');
else
    fprintf('Arbitrary...');
end
startTime = tic;

fclose('all');
filePath_cell = cell(numOfChannels,1);
for channel_idx = 1:numOfFiles
    amplitude1_sign = sign(amplitude1);
    amplitude2_sign = sign(amplitude2);
    amplitude1_fix = amplitude1_sign * round(abs(amplitude1));
    amplitude2_fix = amplitude2_sign * round(abs(amplitude2));
    isZero = amplitude1 == 0;
    if isZero
        fileName = 'pattern_zero.pat';
        fprintf('"%s"...',fileName);
    else
        if numOfFiles > 0
            channelNum = channel_arr(channel_idx);
            fileName = sprintf('pattern_CH%02d.pat',channelNum);
            fprintf('%d...',channelNum);
        else
            fileName = 'pattern.pat';
            fprintf('"%s"...',fileName);
        end
    end
    filePath = fullfile(cd,fileName);
    delete(filePath);
    filePath_cell{channel_idx} = filePath;

    % isMatching = false;
    % while ~isMatching
        %% Write to Pattern File
        patternFile = -1;
        while patternFile < 0
            [patternFile,err] = fopen(filePath,'w+');
            if ~isempty(err)
                delete(filePath);
            % else
            %     break;
            end
        end

        fprintf(patternFile,'variable\n');
        if amplitude1_fix == 0 && amplitude2_fix == 0
            fprintf(patternFile,'%d\n',zero_current);
            fprintf(patternFile,'%d\n',testWidth);
        else
            fprintf(patternFile,'%d\n',amplitude1_fix);
            fprintf(patternFile,'%d\n',phaseWidth1);
            if hasInterphaseDelay
                fprintf(patternFile,'%d\n',zero_current);
                fprintf(patternFile,'%d\n',interphaseDelay);
            end
            fprintf(patternFile,'%d\n',amplitude2_fix);
            fprintf(patternFile,'%d\n',phaseWidth2);
            if isTriphasic
                if hasInterphaseDelay
                    fprintf(patternFile,'%d\n',zero_current);
                    fprintf(patternFile,'%d\n',interphaseDelay);
                end
                fprintf(patternFile,'%d\n',amplitude3_fix);
                fprintf(patternFile,'%d\n',phaseWidth3);
            end
            if hasDischargeDelay
                fprintf(patternFile,'%d\n',zero_current);
                fprintf(patternFile,'%d\n',dischargeDelay);
            end
        end
        fclose(patternFile);

end
[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

end