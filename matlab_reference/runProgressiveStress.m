function [File,isQuit] = runProgressiveStress(File)
close all;
close(findall(0,'type','figure'));
warning('off','all');
isQuit = false;
stopStimulation();
try
    %% Variables
    fields = fieldnames(File);
    notebook = File.Notebook;
    subject = File.Subject;
    emailAddress = File.User.Email;
    phone = File.User.Phone;
    carrier = File.User.Carrier;
    saveFolder = File.Path;
    target = File.Test.ChargePhase;
    if isinf(target)
        stimType = 'MAX';
    else
        stimType = sprintf('%gnC',target);
    end
    if ~isempty(subject)
        Name = [notebook '_' subject];
    else
        expName = notebook;
    end
    foldername = expName;
    fileName = expName;
    file_zip = '';

    % Device
    deviceType = File.Parameters.Device;

    % Environment
    environment = File.Parameters.Environment;

    % Channels
    plexonChannel_arr = File.Parameters.Channels.Plexon;
    testChannel_arr = File.Parameters.Channels.Test;

    % Surface Area
    surfaceArea_arr = File.Parameters.SurfaceArea;
    numOfSurfaceArea = length(surfaceArea_arr);

    % Electrode
    activeElectrode = File.Parameters.WorkingElectrode.Type;
    refElectrode = File.Parameters.ReferenceElectrode.Type;
    returnElectrode = File.Parameters.CounterElectrode.Type;
    channelGroup_mat = File.Test.Groups;
    [numOfGroups,numOfChannelsUsed] = size(channelGroup_mat);

    % Test Type
    testType = File.Test.ID;
    chargePhaseTarget = File.Test.ChargePhase;
    isTargetLimit = isinf(chargePhaseTarget);
    isTargetMax = contains2(testType,'max');
    File.Test.TimeStep = timeStep;
    % Oscillscopes
    numOfDevices = length(File.Oscilloscope);
    isUSB_tf = zeros(numOfDevices,1);
    for deviceNum = 1:numOfDevices
        resourceName = File.Oscilloscope(deviceNum).Resource;
        isUSB_tf(deviceNum) = contains2(resourceName,'usb');
    end
    isOnlyUSB = all(isUSB_tf);
    fieldsList = vertcat(File.Oscilloscope(:).Fields);
    activeChannel_tf = containsi(fieldsList,{'act','work','pot'});
    diffChannel_tf = containsi(fieldsList,{'diff'});
    voltageChannel_tf = containsi(fieldsList,{'volt'});
    if any(activeChannel_tf)
        specialChannel_idx = find(activeChannel_tf);
    elseif any(diffChannel_tf)
        specialChannel_idx = find(diffChannel_tf);
    elseif any(voltageChannel_tf)
        specialChannel_idx = find(voltageChannel_tf);
    else
        specialChannel_idx = 1;
    end
    specialChannel = fieldsList(specialChannel_idx);
    isVoltageSpecial = contains2(specialChannel,'volt');
    hasReturnChannel = contains2(fieldsList,{'ret','count'});
    hasAltActive = isVoltageSpecial && hasReturnChannel;

    % Fields
    Capture = File.Data(1).Capture;
    field_cell = fieldnames(Capture);
    timeField_idx = find(containsi(field_cell,'Time'));
    currentDensity_idx = find(containsi(field_cell,'CurrentDensity'));
    dataFields_cell = field_cell(timeField_idx+1:currentDensity_idx-1);
    activeField_tf = containsi(dataFields_cell,{'act','work','pot'});
    hasActiveField = any(activeField_tf);
    if hasAltActive || hasActiveField || hasDiffField
        electrode_use = 'reference';
    elseif hasVoltageField
        electrode_use = 'counter';
    end

    % Pulsing
    filePath_old = fullfile(saveFolder,foldername);
    filePath = mkdir2(filePath_old);
    [~,foldername,~] = fileparts(filePath);
    fileName = foldername;
    emailSubject = sprintf('MATLAB Progressive Stress: %s',fileName);
    file_tif_cell = {};

    % Progress Bar
    prog = 0;
    progMsg = sprintf('Progressive Stress: %d / %d (%.2f%%)',prog,numOfGroups,prog);
    progBar = waitbar(prog,progMsg,'Name','Voltage Transient Progress');

    % Status
    Status = struct( ...
        'Description','', ...
        'Good',true, ...
        'Normal',true, ...
        'PotentialLimit',0, ...
        'VoltageSafety',true, ...
        'VoltageCompliance',false, ...
        'MaxCurrent',false, ...
        'TooLong',false, ...
        'Quit',false);

    isOn = File.Stimulator.Status;
    if isOn
        %% Prepare stimulation for each channel
        % Stimulation parameters
        phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
        interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
        phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
        stimRate = File.Parameters.StimulationRate;
        polarity = File.Parameters.Polarity;
        isSymmetric = File.Parameters.Symmetry;
        pattern = struct( ...
            'A1',0, ...
            'A2',0, ...
            'W1',phaseWidth1, ...
            'W2',phaseWidth2, ...
            'Delay',interphaseDelay);
        numOfPulses = File.Parameters.NumberOfPulses;     % number of pulses
            maxChannelTime = Inf;
        amplitude1_arr_old = File.Parameters.Amplitude1;
        amplitude2_arr_old = File.Parameters.Amplitude2;

        % Set stimulation rate
        isQuit = setAllStimRate(stimRate);
        if isQuit
            return;
        end

        % Set number of repetitions for all stimulators
        isQuit = setAllNumOfPulses(numOfPulses);
        if isQuit
            return;
        end
        fprintf('\n');

        %% Stimulation
        startTime = tic;
        for groupNum = 1:numOfGroups % consecutively stimulate channels
            clc;
            % Channel connection
            channelNum = channelGroup_mat(groupNum,1);
            % plexonChannel = plexonChannel_arr(channelNum);
            channelName = sprintf('Channel %02d',channelNum);
            channel_tf = testChannel_arr == channelNum;
            fprintf('%s Channel %d...',deviceType,channelNum);
            channelStim = plexonChannel_arr(channelNum);
            fprintf('Plexon Channel %d\n',channelStim);
            File.Data(groupNum).ActiveChannel = channelNum;
            if numOfSurfaceArea > 1
                surfaceArea = surfaceArea_arr(channel_tf);
            else
                surfaceArea = surfaceArea_arr;
            end
            File.Data(groupNum).SurfaceArea = surfaceArea;
            targetCharge = File.Test.ChargePhase;
            targetAmplitude = roundStim(targetCharge / phaseWidth1 * 1e3);
            stepSize = File.Test.StepSize;
            hasStepSize = any(stepSize);
            amplitude1_arr = amplitude1_arr_old;
            amplitude2_arr = amplitude2_arr_old;
            File.Parameters.Amplitude1 = amplitude1_arr;
            File.Parameters.Amplitude2 = amplitude2_arr;

            % Set the monitor channel
            channelTitle = channelName;
            isQuit = setMonitorChannel(File,channelNum);
            if isQuit
                break;
            end
            channelReturn_arr = [];
            channelName_use = channelTitle;
            channelName_use = erase(channelName_use,'Channel ');
            channelName_use = strrep(channelName_use,' ','_');
            channelName_use = strrep(channelName_use,'versus','v');
            channelName_use = erase(channelName_use,',');
            File.Data(groupNum).Name = channelTitle;
            File.Data(groupNum).ID = channelName_use;
            progFract = (groupNum - 1) / numOfGroups;
            progPercent = progFract * 100;
            progMsg = sprintf('Progressive Stress: %d / %d (%.2f%%)', ...
                groupNum-1,numOfGroups,progPercent);
            channelTitle_msg = [channelTitle '...'];
            progMsg_cell = {progMsg,channelTitle_msg};
            waitbar(progFract,progBar,progMsg_cell);

            % Initialize View
            amplitude1 = amplitude1_arr(channel_tf);
            amplitude1_mag = abs(amplitude1);
            File = setOscilloscopeStatus(File,'open');
            setOscillocopeView(File,0);
            currentStim1 = roundStim(amplitude1_mag);
            isTargetFound = false;
            isVoltageBad = false;
            captureNum = 0;
            currentChange = amplitude1;

            beep;
            startChannelTime = tic;
            while ~isTargetFound
                startPrepTime = tic;
                captureNum = captureNum + 1;
                File.Data(groupNum).Capture(captureNum).Index = captureNum;
                File.Data(groupNum).Capture(captureNum).Status = Status;

                % Stimulation change
                currentStim1 = roundStim(currentStim1);
                if ~any(currentStim1)
                    currentStim1 = 0.1;
                end
                currentStim2 = roundStim(currentStim1 * phaseWidth1 / phaseWidth2);
                if currentStim1 >= 1e3 || currentStim2 >= 1e3
                    fprintf('Max current reached...');
                    if currentStim1 >= 1e3
                        currentStim1 = 1e3;
                        if ~isSymmetric
                            charge1 = currentStim1 * phaseWidth1 / phaseWidth2;
                            currentStim2 = roundStim(charge1);
                        end
                    end
                    if currentStim2 >= 1e3
                        currentStim2 = 1e3;
                        if ~isSymmetric
                            charge2 = currentStim2 * phaseWidth2 / phaseWidth1;
                            currentStim1 = roundStim(charge2);
                        end
                    end
                    isAtMaxCurrent = true;
                    File.Data(groupNum).Capture(captureNum). ...
                        Status.MaxCurrent = isAtMaxCurrent;
                    fprintf('OK\n');
                else
                    isTargetFound = false;
                    isAtMaxCurrent = false;
                    File.Data(groupNum).Capture(captureNum). ...
                        Status.MaxCurrent = false;
                    File.Data(groupNum).Capture(captureNum). ...
                        Status.Description = 'Good';
                end
                % new amplitude
                amplitude1_new = roundStim(polarity * currentStim1);
                isAtFixedTarget = currentStim1 == targetAmplitude;
                amplitude2_new = roundStim(-polarity * currentStim2);
                charge1 = amplitude1_new * phaseWidth1;
                charge2 = amplitude2_new * phaseWidth2;
                isBalanced = charge1 + charge2 == 0;
                if ~isBalanced
                    currentStim1 = currentStim2 * phaseWidth2 / phaseWidth1;
                    amplitude1_new = roundStim(polarity * currentStim1);
                end
                File.Data(groupNum).Capture(captureNum).Amplitude = amplitude1_new;
                File.Data(groupNum).Capture(captureNum).PhaseWidth = phaseWidth1;
                File.Data(groupNum).Capture(captureNum).CurrentChange = currentChange;
                File.Parameters.Amplitude1(channel_tf) = amplitude1_new;
                File.Parameters.Amplitude2(channel_tf) = amplitude2_new;
                if captureNum > 1
                    amplitude_arr = [File.Data(groupNum).Capture(:).Amplitude];
                    % currentStimList = abs(amplitude_arr);
                    isAmplitudeRepeat_tf = ismember(amplitude_arr,amplitude1_new);
                    isAmplitudeRepeat_idx = find(isAmplitudeRepeat_tf);
                    isAmplitudeRepeat = length(isAmplitudeRepeat_idx) > 3;
                    % isAmplitudeRepeat = any(ismember(amplitude1,amplitude_arr));
                else
                    isAmplitudeRepeat = false;
                end
                charge1 = round(amplitude1_new * phaseWidth1);
                charge2 = round(amplitude2_new * phaseWidth2);
                isBalanced = charge1 + charge2 == 0;
                if ~isBalanced
                    fprintf('Charge 1 = %f\n',charge1);
                    fprintf('\tAmplitude 1: %f\n',amplitude1_new);
                    fprintf('\tPhase Width 1 = %f\n',phaseWidth1);
                    fprintf('Charge 2 = %f\n',charge2);
                    fprintf('\tAmplitude 2: %f\n',amplitude2_new);
                    fprintf('\tPhase Width 2 = %f\n',phaseWidth2);
                    error('Not charge-balanced!');
                end

                % Set rectangular pulse parameters
                pattern.A1 = amplitude1_new;   	% first phase amplitude
                pattern.A2 = amplitude2_new; 	% second phase amplitude

                % Set stimulation parameters
                isQuit = setPattern(File,channelNum,pattern,channelReturn_arr);
                if isQuit
                    break;
                end

                % Electrode
                File.Data(groupNum).Active = activeElectrode;
                File.Data(groupNum).Return = returnElectrode;
                File.Data(groupNum).Reference = refElectrode;
                if isVoltageSpecial
                    electrode = refElectrode;
                else
                    electrode = returnElectrode;
                end
                    refElectrode_use = sprintf('%s in electrolyte',electrode);
                File.Data(groupNum).Reference = refElectrode_use;

                % Load parameters to channel
                %                 isQuit = loadAllChannels(1);
                isQuit = loadPattern(File,channelReturn_arr);
                if isQuit
                    break;
                end

                % Start Stimulation
                fprintf('Stimulation intensity:\n');
                % Amplitude
                fprintf('\tIstim(1) = %.2f uA\n',amplitude1_new);
                if ~isSymmetric
                    fprintf('\tIstim(2) = %.2f uA\n',amplitude2_new);
                end

                % Charge
                [chargePhase,chargeInjection] = getCharge( ...
                    currentStim1,phaseWidth1,surfaceArea);
                fprintf('\tQph = %g nC/ph\n',chargePhase);
                fprintf('\tQinj = %g mC/cm2\n',chargeInjection);
                % charge/phase
                File.Data(groupNum).Capture(captureNum). ...
                    ChargePhase = chargePhase;
                % charge injection
                File.Data(groupNum).Capture(captureNum). ...
                    ChargeInjection = chargeInjection;

                % Stimulation
                isQuit = startStimulation(File,channelReturn_arr);
                if isQuit
                    return;
                end

                % Set View
                %                 fprintf('\t');
                amplitude_fix = max([currentStim1 currentStim2]);
                setOscilloscopeCurrentScale(File,amplitude_fix);
                [endPrepTime,unit] = getEndTime(startPrepTime);
                fprintf('Preparation Time: %.2f %s\n',endPrepTime,unit);
                fprintf('\n');

                % Capture waveform
                fprintf('Capture Number: %d\n',captureNum);
                %                 pause(3);
                dateTime = getDateTime();
                File.Data(groupNum).Capture(captureNum). ...
                    DateTime = dateTime;    % date and time completed
                [File,buttonHandle] = getWaveformData(File);
                elapsedChannelTime = toc(startChannelTime);
                if elapsedChannelTime < maxChannelTime
                    stopStimulation();
                end
                isAtVoltageCompliance = File.Data(groupNum). ...
                    Capture(captureNum).Status.VoltageCompliance;
                isVoltageSafe = File.Data(groupNum).Capture(captureNum). ...
                    Status.VoltageSafety;
                isLimitReached = File.Data(groupNum).Capture(captureNum). ...
                    Status.PotentialLimit;
                isQuit = File.Data(groupNum).Capture(captureNum).Status.Quit;
                isVoltageBad = isAtVoltageCompliance || ~isVoltageSafe;
                isVoltageStopped = isVoltageBad || isLimitReached;
                if isQuit  	% stop by button handle
                    fprintf('OK\n');
                    break;
                end
                if isLimitReached
                    switch isLimitReached
                        case -1
                            fprintf('Cathodic potential limit reached...');
                        case 1
                            fprintf('Anodic potential limit reached...');
                    end
                    isTargetFound = true;
                    fprintf('OK\n');
                end
                if isVoltageBad
                    if isAtVoltageCompliance
                        fprintf('Voltage compliance reached...');
                    elseif ~isVoltageSafe
                        fprintf('Voltage is unsafe...');
                    end
                    isTargetFound = true;
                    fprintf('OK\n');
                end

                % Forcing channel stop
                if (elapsedChannelTime > maxChannelTime || isAmplitudeRepeat)...
                        && ~isLimitReached
                    [endChannelTime,unit] = getEndTime(startChannelTime);
                    if isAmplitudeRepeat
                        fprintf('Not enough precision (%.2f %s)...',endChannelTime,unit);
                        discription = 'Not enough precision';
                        File.Data(groupNum).Capture(captureNum). ...
                            Status.Precise = false;
                    else
                        fprintf('Took too long (%.2f %s)...',endChannelTime,unit);
                        discription = 'Took to long';
                        File.Data(groupNum).Capture(captureNum). ...
                            Status.TooLong = true;
                    end
                    File.Data(groupNum).Capture(captureNum). ...
                        Status.Description = discription;
                    File.Data(groupNum).Capture(captureNum).Status.Good = false;
                    fprintf('OK\n');
                    fprintf('Capture Number: %d\n',captureNum);
                    [File,buttonHandle,isQuit] = getWaveformData(File);
                    stopStimulation();
                    isTargetFound = true;
                end
                if isQuit  	% stop by button handle
                    fprintf('OK\n');
                    break;
                end
                if isVoltageBad
                    if isAtVoltageCompliance
                        fprintf('Voltage compliance reached...');
                    elseif ~isVoltageSafe
                        fprintf('Voltage is unsafe...');
                    end
                    isTargetFound = true;
                    fprintf('OK\n');
                end
                if isLimitReached
                    switch isLimitReached
                        case -1
                            fprintf('Cathodic potential limit reached...');
                        case 1
                            fprintf('Anodic potential limit reached...');
                    end
                    isTargetFound = true;
                    fprintf('OK\n');
                end
                if isAtFixedTarget
                    fprintf('Fixed target reached...');
                    isTargetFound = true;
                    fprintf('OK\n');
                end

                % Adjust Current
                if ~isVoltageStopped && ~isTargetFound
                    if (isTargetMax || (~isAtFixedTarget && hasStepSize))
                        [File,currentStim1,currentChange] = changeCurrent_Fit(File);
                        currentStim2 = roundStim(currentStim1 * phaseWidth1 / phaseWidth2);
                        if (currentStim1 >= 1e3 || currentStim2 >= 1e3) && isAtMaxCurrent
                            fprintf('Current cannot be any larger...');
                            isTargetFound = true;
                            File.Data(groupNum).Capture(captureNum). ...
                                Status.Description = 'Max current reached';
                            File.Data(groupNum).Capture(captureNum). ...
                                Status.MaxCurrent = true;
                            fprintf('OK\n');
                        end
                    elseif ~hasStepSize || isVoltageBad
                        isTargetFound = true;
                    end
                else
                    isTargetFound = true;
                end
                if isTargetFound
                    break;
                end
            end
            %     fprintf('\n');
            if isQuit
                fprintf('OK\n\n');
                break;
            end
            %         end

            %% Store data in structure
            % Store Values
            % Capture
            File.Data(groupNum).Capture(captureNum). ...
                Amplitude = amplitude1_new;
            File.Data(groupNum).Capture(captureNum). ...
                ChargePhase = chargePhase;       	    % charge per phase
            File.Data(groupNum).Capture(captureNum). ...
                ChargeInjection = chargeInjection;
            % Group Number
            File.Data(groupNum).Amplitude = amplitude1_new;
            File.Data(groupNum).ChargePhase = chargePhase;       	    % charge per phase
            File.Data(groupNum).ChargeInjection = chargeInjection;
            [isLimitReached_check,~] = checkPotentialExcursion(File,electrode_use);
                channelMapping = File.Parameters.Mapping.Channel;
                channelMapping_idx = find(channelMapping == channelNum);
                File.Parameters.Mapping.Amplitude(channelMapping_idx) = amplitude1_new;
                File.Parameters.Mapping.ChargePhase(channelMapping_idx) = chargePhase;
                File.Parameters.Mapping.ChargeInjection(channelMapping_idx) = chargeInjection;
                if ~isTargetMax && ~isLimitReached_check ...
                        || isAtVoltageCompliance || isVoltageBad
                    amplitude1_arr(channel_tf) = NaN;
                    amplitude2_arr(channel_tf) = NaN;
                else
                    amplitude1_arr(channel_tf) = amplitude1_new;
                    amplitude2_arr(channel_tf) = amplitude2_new;
                end
            dateTime = getDateTime();
            File.Data(groupNum).Capture(captureNum).DateTime = dateTime; % date and time completed
            File.Data(groupNum).DateTime = dateTime;
            if isgraphics(buttonHandle)
                if ishandle(buttonHandle)
                    try
                        buttonHandle.Visible = 'off';
                        delete(buttonHandle);
                    catch
                        delete(buttonHandle);
                    end
                    % File.Data(groupNum).Figure = figure(groupNum);
                end
            end
            elapsedChannelTime = toc(startChannelTime);
            File.Data(groupNum).TimeElapased = elapsedChannelTime;
            status = File.Data(groupNum).Capture(captureNum).Status.Description;
            File.Data(groupNum).Status = status;

            %% Saving files
            File.Parameters.Amplitude1 = amplitude1_arr;
            File.Parameters.Amplitude2 = amplitude2_arr;
            if ishandle(figure(groupNum))
                file_tif = saveChannelFigure( ...
                    File, ...
                    fileName, ...
                    filePath);
            else
                file_tif = [];
            end
            dateTimeModified = getDateTime();
            File.DateTimeModified = dateTimeModified;
            %     fprintf('Saving Channel %d...\n',channelNum);
            [file_mat,file_xlsx] = saveVoltageTransientData( ...
                File, ...
                fileName, ...
                filePath);

            % Message
            channelID = File.Data(groupNum).ID;
            msg = sprintf('%s (%d / %d)',channelID,groupNum,numOfGroups);
            sendPictureText(File,emailSubject,msg,file_tif);
            %     fprintf('\n');

            file_tif_cell_alloc = [file_tif_cell;file_tif];
            file_tif_cell = file_tif_cell_alloc;
            %     fprintf('\n');
            %% Change channel
            progFract = groupNum / numOfGroups;
            progPercent = progFract * 100;
            progMsg = sprintf('Progressive Stress: %d / %d (%.2f%%)', ...
                groupNum,numOfGroups,progPercent);
            channelTitle_msg = [channelTitle '...' status];
            progMsg_cell = {progMsg,channelTitle_msg};
            waitbar(progFract,progBar,progMsg_cell);
            [endChangeTime,unit] = getEndTime(startChannelTime);
            File.Data(groupNum).TimeElapse = sprintf('%.2f %s', ...
                endChangeTime,unit);
            [isAgain,isStop] = changingChannel( ...
                File, ...
                startTime,startChannelTime);
            if isAgain
                groupNum = groupNum - 1; %#ok<*FXSET
                File.Data(groupNum).Capture = [];
            end
            if isStop  % stop stimulation after current channel
                break;          % quit after this channel
            else
                fprintf('\n');
            end
        end
        progFract = groupNum / numOfGroups;
        progPercent = progFract * 100;
        progMsg = sprintf('Progressive Stress: %d / %d (%.2f%%)', ...
            groupNum,numOfGroups,progPercent);
        channelTitle_msg = strrep(['"' fileName '"' '...Done!'],'_','\_');
        progMsg_cell = {progMsg,channelTitle_msg};
        waitbar(progFract,progBar,progMsg_cell);
        File = setOscilloscopeStatus(File,'close');
        if isQuit
            return;
        end

        %% Email
        if ~isempty(emailAddress) || (~isempty(phone) && ~isempty(carrier))
            dirFile = dir(filePath);
            dirFileByte_arr = [dirFile(:).bytes];
            dirFileByte_max = sum(dirFileByte_arr);
            dirFileMByte_max = dirFileByte_max / 2^20;
            isFileSmall = false;
            if dirFileMByte_max < 25
                isFileSmall = true;
                files_cell = {file_xlsx;file_mat;file_tif_cell};
            else
                dirFile_xlsx = dir(file_xlsx);
                dirFile_mat = dir(file_mat);
                dirFileByte = dirFile_xlsx.bytes + dirFile_mat.bytes;
                dirFileMByte = dirFileByte / 2^20;
                if dirFileMByte < 25
                    isFileSmall = true;
                    files_cell = {file_xlsx;file_mat};
                end
            end
            if isFileSmall
                zipFilename = [fileName '.zip'];       	% fileName for .zip file
                file_zip = fullfile(filePath,zipFilename);  % save path for .zip file
                fprintf('Compressing files...');
                startCompressTime = tic;
                % try
                zip(file_zip,files_cell)
                attachements = file_zip;
                [endCompressTime,unit] = getEndTime(startCompressTime);
                fprintf('%s (%.2f %s)\n',zipFilename,endCompressTime,unit);
            else
                file_zip = [];
                attachements = [file_mat;file_tif_cell];
            end

            [endTime,unit] = getEndTime(startTime);
            sendMessage(File,emailSubject,attachements,endTime,unit);
        end
        if ~isempty(file_zip)
            startDeleteTime = tic;
            fprintf('Deleting %s...',zipFilename);
            delete(file_zip);
            [endDeleteTime,unit] = getEndTime(startDeleteTime);
            fprintf('OK (%.2f %s)\n',endDeleteTime,unit);
        end
        %         fprintf('\n');
    end

catch err
    fprintf('\n');
    stopStimulation();
    beep;pause(0.5);
    beep;pause(0.5);
    beep;
    File = setOscilloscopeStatus(File,'close');
    fields_check = fieldnames(File);
    if contains2(fields_check,'Error')
        idx = 2;
    else
        idx = 1;
    end
    File.Error(idx).Status = err;
    report = getReport(err);
    report_short = getReport(err,'extended','hyperlinks','off');
    File.Error(idx).Report = report;
    display(report);

    varName = getVarName(File);
    newVarName = ['FailedFile_' fileName];
    setNewVarName = sprintf('%s = %s;',newVarName,varName);
    eval(setNewVarName);
    fileName_mat = [fileName '.mat'];       	% fileName for .mat file
    tempName_mat = [fileName '_temp.mat'];
    filePath_mat = fullfile(filePath,fileName_mat);  % save path for .mat file
    tempPath_mat = fullfile(filePath,tempName_mat);
    attachements = filePath_mat;
    try
        save(filePath_mat,newVarName);             	% save .mat file
    catch
        save(tempPath_mat,newVarName);
        delete(filePath_mat);
        movefile(tempPath_mat,filePath_mat,'f');
    end
    emailSubject = sprintf('MATLAB Progressive Stress Failed: %s',fileName);
    [endTime,unit] = getEndTime(startTime);
    sendError(File,emailSubject,report_short,attachements,endTime,unit);

    isQuit = true;
end
fprintf('\n');

end