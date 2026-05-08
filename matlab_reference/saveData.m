function [filepath_mat,filepath_xlsx] = saveData(...
    File,...
    filename,...
    filepath)
%% Variables
% Initialize Arrays
Data = File.Data;
numOfChannels = length(Data);
zero_mat = zeros(numOfStimRate,numOfChannels);
channelName_arr = cell(1,numOfChannels);
amplitude_mat = zero_mat;
chargePhase_mat = zero_mat;
chargeInjection_mat = zero_mat;
potentialExcursion_mat = zero_mat;
accessVoltage_mat = zero_mat;
drivingVoltage_mat = zero_mat;


%% File Names
% Starting filename
filenameAll_char = filename;

% .mat filename
filename_mat = append(filenameAll_char,'.mat');       	% filename for .mat file
filepath_mat = fullfile(filepath,filename_mat);  % save path for .mat file

% .xlsx filename
filename_xlsx = append(filenameAll_char,'.xlsx');           % filename for .xlsx file
filepath_xlsx = fullfile(filepath,filename_xlsx); 	% save path for .xlsx file

%% Save
% Making .mat file
fprintf('Saving MATLAB file...');
save(filepath_mat,'File');             	% save .mat file
fprintf('"%s"\n',filename_mat);         	% .mat file saved

% Making spreadsheet files
fprintf('Saving Excel file...');
for channelNum = 1:numOfChannels
    % loop through channel data
    % Capture data of channel
    time_arr = File.Data(channelNum).Time ;              % time data array
    voltage_mat = File.Data(channelNum).Voltage;            % voltage data array
    current_mat = File.Data(channelNum).Current;          % current data array
    amplitude_arr = File.Data(channelNum).Amplitude;      	% amplitude value
    chargePhase_arr = File.Data(channelNum).ChargePhase;      	% charge per phase value
    surfaceArea = File.Data(channelNum).SurfaceArea;% geometric surface area
    chargeInjection_arr = File.Data(channelNum).ChargeInjection;           % charge injection value
    potentialExcursion_mat = File.Data(channelNum).PotentialExcursion;   % maximum cathodal potential value
    accessVoltage_mat = File.Data(channelNum).AccessVoltage;
    drivingVoltage_mat = File.Data(channelNum).DrivingVoltage;           % driving voltage value
    dateTime = File.Data(channelNum).DateTime;                   % date and time completed
    status = File.Data(channelNum).Status.Type;

    % Initialize Arrays
    data_len = length(time_arr);
    cell_arr = cell(data_len,1);	% array of blank space
    amplitude_cell = cell_arr;
    chargePhase_cell = cell_arr;
    surfaceArea_cell = cell_arr;
    chargeInjection_cell = cell_arr;
    potentialExcursion_cell = cell_arr;
    accessVoltage_cell = cell_arr;
    drivingVoltage_cell = cell_arr;
    dateTime_cell = cell_arr;
    status_cell = cell_arr;

    % Potential Excursion Unit
    if any(potentialExcursion_mat < 0)
        potentialExcursionTitle = 'Emc (V)';
    else
        potentialExcursionTitle = 'Ema (V)';
    end

    % Index
    if numOfStimRate > 1
        data_cell = cell(data_len,numOfStimRate*2);
        for stimRateNum = 1:numOfStimRate
            stimRate = stimRate_arr(stimRateNum);
            % Extract values
            voltage_arr = voltage_mat(:,stimRateNum);            % voltage
            current_arr = current_mat(:,stimRateNum);             % current
            amplitude = amplitude_arr(stimRateNum);      	    % amplitude
            chargePhase = chargePhase_arr(stimRateNum);         % charge/phase value
            chargeInjection = chargeInjection_arr(stimRateNum); % charge injection
            potentialExcursion_arr = potentialExcursion_mat(1,stimRateNum);   % potential excursion
            accessVoltage = accessVoltage_mat(1,stimRateNum);         % access voltage
            drivingVoltage = drivingVoltage_mat(1,stimRateNum);           % driving voltage

            % Add to cells
            odd_idx = stimRateNum * 2 -1;
            even_idx = data_cell * 2;
            data_cell(:,odd_idx) = voltage_arr;
            data_cell(:,even_idx) = current_arr;
            amplitude_cell{stimRateNum} = amplitude;
            chargePhase_cell{stimRateNum} = chargePhase;
            surfaceArea_cell{stimRateNum} = surfaceArea;
            chargeInjection_cell{stimRateNum} = chargeInjection;
            potentialExcursion_cell{stimRateNum} = potentialExcursion_arr{1};
            accessVoltage_cell{stimRateNum} = accessVoltage_arr{1};
            drivingVoltage_cell{stimRateNum} = drivingVoltage_arr{1};

            amplitude_mat(stimRateNum,channnelNum) = amplitude;
            chargePhase_mat(stimRateNum,channelNum) = chargePhase;
            chargeInjection_mat(stimRateNum,channelNum) = chargeInjection;
            potentialExcursion_mat(stimRateNum,channelNum) = potentialExcursion;
            accessVoltage_mat(stimRateNum,channelNum) = accessVoltage;
            drivingVoltage_mat(stimRateNum,channelNum) = drivingVoltage;
        end

        % Single Values
        dateTime_cell{1} = dateTime;
        status_cell{1} = status;

        % Table
        tableHeading = {'Time (us)'};     % time
        for heading = 1:numOfStimRate*2
            stimRate_use = addCommas(stimRate);
            stimVoltage = sprintf('%s (V)',stimRate_use);
            stimCurrent = sprintf('%s (A)',stimRate_use);
            tableHeading_alloc = [tableHeading,stimVoltage,stimCurrent];     % time
            tableHeading = tableHeading_alloc;
        end
        tableHeading_alloc = [tableHeading,...
            'Amplitude (uA)','Qph (nC/ph)','Area (um2)','Qinj (mC/cm2)',...
            potentialExcursionTitle,'Vacc (V)','Vdrive (V)',...
            'Date Time','Status'];
        tableHeading = tableHeading_alloc;

        tableData = table(...               % table
            time_arr,...                   % time
            data_cell,...                % voltage transient
            amplitude_cell,...
            chargePhase_cell,...         % Qph
            surfaceArea_cell,...     % GSA
            chargeInjection_cell,...           % Qinj
            potentialExcursion_cell,...        % Emc
            accessVoltage_cell,...
            drivingVoltage_cell,...        % Vdrive
            dateTime_cell,...            % DateTime
            status_cell,...
            'VariableNames',tableHeading);
    else
        voltage_arr = voltage_mat;            % voltage
        current_arr = current_mat;             % current
        amplitude = amplitude_arr;      	        % amplitude
        chargePhase = chargePhase_arr;      	% charge/phase value
        chargeInjection = chargeInjection_arr;           % charge injection
        potentialExcursion_arr = potentialExcursion_mat;   % potential excursion
        accessVoltage_arr = accessVoltage_mat;         % access voltage
        drivingVoltage_arr = drivingVoltage_mat;           % driving voltage

        % Single Values
        amplitude_cell{1} = amplitude;
        chargePhase_cell{1} = chargePhase;
        surfaceArea_cell{1} = surfaceArea;
        chargeInjection_cell{1} = chargeInjection;
        dateTime_cell{1} = dateTime;
        status_cell{1} = status;

        % Multiple Values
        numOfValues = length(potentialExcursion_mat);
        for idx = 1:numOfValues
            potentialExcursion_cell{idx} = potentialExcursion_arr{idx};
            accessVoltage_cell{dix} = accessVoltage_arr{idx};
            drivingVoltage_cell{idx} = drivingVoltage_arr{idx};
        end

        % Table
        tableHeading = {...     % heading
            'Time (us)',...     % time
            'Voltage (V)',...   % voltage transient
            'Current (uA)',...  % current stimulation
            'Amplitude (uA)',...
            'Qph (nC/ph)',...   % charge per phase
            'Area (um2)',...     % geometric surface area
            'Qinj (mC/cm2)',... % charge injection
            potentialExcursionTitle,...       % max potential
            'Vacc (V)',...
            'Vdrive (V)',...    % driving voltage
            'Date Time',...% date and time
            'Status'};
        tableData = table(...               % table
            time_arr,...                   % time
            voltage_arr,...                % voltage transient
            current_arr,...                % current stimulation
            amplitude_cell,...
            chargePhase_cell,...         % Qph
            surfaceArea_cell,...     % GSA
            chargeInjection_cell,...           % Qinj
            potentialExcursion_cell,...        % Emc
            accessVoltage_cell,...
            drivingVoltage_cell,...        % Vdrive
            dateTime_cell,...            % DateTime
            status_cell,...
            'VariableNames',tableHeading);
    end

    % Save sheet
    channelNum_char = sprintf('Channel %d',channelNum);     % channel number (char)
    channelNum_str = convertCharsToStrings(channelNum_char);% channel number (string)
    writetable(tableData,filepath_xlsx,'Sheet',channelNum_str);    % save sheet to .xlsx file
    channelName_arr{channelNum} = channelNum_char;
    amplitude_mat(channnelNum) = amplitude;
    chargePhase_mat(channelNum) = chargePhase;
    chargeInjection_mat(channelNum) = chargeInjection;
    potentialExcursion_mat(channelNum) = potentialExcursion;
    accessVoltage_mat(channelNum) = accessVoltage;
    drivingVoltage_mat(channelNum) = drivingVoltage;
    fprintf('%d...',channelNum);
end

% Compiled Value Sheet
for stimRateNum = 1:numOfStimRate
    if numOfStimRate > 1
        stimRate = stimRate_arr(stimRateNum);
        stimRate_use = addCommas(stimRate);
        sheetName = sprintf('%s pps',stimRate_use);
        data_array = [...
        amplitudeList(stimRate);chargePhaseList(stimRate);chargeInjectionList(stimRate);...
        potentialExcursionList(stimRate);accessVoltageList(stimRate);drivingVoltageList(stimRate)];
    else
        % Last Sheet
        sheetName = 'Values';
        data_array = [...
        amplitudeList;chargePhaseList;chargeInjectionList;...
        potentialExcursionList;accessVoltageList;drivingVoltageList];
    end
    headings = tableHeading([4 5 7 8 9 10]);
    dataTable = array2table(...
        data_array,...
        'VariableNames',channelName_arr,...
        'RowNames',headings);
    writetable(dataTable,filepath_xlsx,'Sheet',sheetName,'WriteRowNames',true);    % save sheet to .xlsx file
    fprintf('"%s"\n',filename_xlsx);	% .xlsx file saved
end

% Final Sheets
if numOfStimRate > 1
    stimRate_heading = cell(1,numOfStimRate);
    for stimRateNum = 1:numOfStimRate
        stimRate = stimRate_arr(stimRateNum);
        stimRate_use = addCommas(stimRate);
        stimRate_char = sprintf('%s pps',stimRate_use);
        stimRate_heading{stimRateNum} = stimRate_char;
    end

    % Amplitude
    amplitude_table = array2table(amplitude_mat,...
        'VariableNames',channelName_arr,...
        'RowNames',stimRate_heading);
    writetable(amplitude_table,filepath_xlsx,'Sheet','Amplitude');

    % Charge/Phase
    chargePhase_table = array2table(chargePhase_mat,...
        'VariableNames',channelName_arr,...
        'RowNames',stimRate_heading);
    writetable(chargePhase_table,filepath_xlsx,'Sheet','ChargePhase');

    % Charge Injection
    chargeInjection_table = array2table(chargeInjection_mat,...
        'VariableNames',channelName_arr,...
        'RowNames',stimRate_heading);
    writetable(chargeInjection_table,filepath_xlsx,'Sheet','ChargeInjection');

    % Potential Excursion
    potentialExcursion_table = array2table(potentialExcursion_mat,...
        'VariableNames',channelName_arr,...
        'RowNames',stimRate_heading);
    writetable(potentialExcursion_table,filepath_xlsx,'Sheet','PotentialExcursion');

    % Access Voltage
    accessVoltage_table = array2table(accessVoltage_mat,...
        'VariableNames',channelName_arr,...
        'RowNames',stimRate_heading);
    writetable(accessVoltage_table,filepath_xlsx,'Sheet','AccessVoltage');

    % Driving Voltage
    drivingVoltage_table = array2table(drivingVoltage_mat,...
        'VariableNames',channelName_arr,...
        'RowNames',stimRate_heading);
    writetable(drivingVoltage_table,filepath_xlsx,'Sheet','DrivingVoltage');
end

end