function [filename_mat_save,filename_xlsx_save] = saveAcuteData3(...
    File,...
    filename,...
    filepath)
%% Function
% Starting filename
% filenameAll_char = append(filename_char,'_ALL'); 	% filename with all channels
filenameAll_char = filename;

% Making .mat file
fprintf('Saving MATLAB file...');
filename_mat = append(filenameAll_char,'.mat');       	% filename for .mat file
filename_mat_save = fullfile(filepath,filename_mat);  % save path for .mat file
save(filename_mat_save,'File');             	% save .mat file
fprintf('"%s"\n',filename_mat);         	% .mat file saved

% Making spreadsheet files
fprintf('Saving Excel file...');
filename_xlsx = append(filenameAll_char,'.xlsx');           % filename for .xlsx file
filename_xlsx_save = fullfile(filepath,filename_xlsx); 	% save path for .xlsx file
channelList = {};
amplitudeList = [];
chargePhaseList = [];
chargeInjList = [];
maxPotentialList = [];
accessVoltageList = [];
drivingVoltageList = [];
for channelNum = 1:16    % loop through channel data
    % Capture data of channel
    time_data = File.Data(channelNum).Time ;              % time data array
    voltage_data = File.Data(channelNum).Voltage;            % voltage data array
    current_data = File.Data(channelNum).Current;          % current data array
    amplitude_data = File.Data(channelNum).Amplitude;      	% amplitude value
    chargePhase_data = File.Data(channelNum).ChargePhase;      	% charge per phase value
    surfaceArea_data = File.Data(channelNum).SurfaceArea;% geometric surface area
    chargeInj_data = File.Data(channelNum).ChargeInjection;           % charge injection value
    maxPotential_data = File.Data(channelNum).PotentialExcursion;   % maximum cathodal potential value
    accessVoltage_data = File.Data(channelNum).AccessVoltage;
    voltageDrive_data = File.Data(channelNum).DrivingVoltage;           % driving voltage value
    dateTime_data = File.Data(channelNum).DateTime;                   % date and time completed
    status_data = File.Data(channelNum).Status;

    % Arrays for single values
    blank_len = length(time_data);          % length of data
    blank_array = strings(blank_len,1);	% array of blank space
    amplitude_strings = blank_array;
    amplitude_strings(1) = string(amplitude_data);     	% add amplitude to array
    chargePhase_strings = blank_array;
    chargePhase_strings(1) = string(chargePhase_data);     	% add charge per phase to array
    surfaceArea_strings = blank_array;
    surfaceArea_strings(1) = string(surfaceArea_data);  % add charge per phase to array
    chargeInj_strings = blank_array;
    chargeInj_strings(1) = string(chargeInj_data);          % add charge injection to array
    maxPotential_strings = blank_array;
    maxPotential_strings(1) = string(maxPotential_data);        % add max potential to array
    accessVoltage_strings = blank_array;
    accessVoltage_strings(1) = string(accessVoltage_data);        % add access voltage to array
    voltageDrive_strings = blank_array;
    voltageDrive_strings(1) = string(voltageDrive_data);        % add driving voltage to array
    dateTime_strings = blank_array;
    dateTime_strings(1) = dateTime_data;                        % add date and time to array
    status_strings = blank_array;
    status_strings(1) = status_data;

    % Table
    if all(maxPotential_data < 0)
        maxPotentialTitle = 'Emc (V)';
    else
        maxPotentialTitle = 'Ema (V)';
    end

    tableHeading = {...     % heading
        'Time (us)',...     % time
        'Voltage (V)',...   % voltage transient
        'Current (uA)',...  % current stimulation
        'Amplitude (uA)',...
        'Qph (nC/ph)',...   % charge per phase
        'Area (um2)',...     % geometric surface area
        'Qinj (mC/cm2)',... % charge injection
        maxPotentialTitle,...       % max potential
        'Vacc (V)',...
        'Vdrive (V)',...    % driving voltage
        'Completed On',...% date and time
        'Status'};
    tableData = table(...               % table
        time_data,...                   % time
        voltage_data,...                % voltage transient
        current_data,...                % current stimulation
        amplitude_strings,...
        chargePhase_strings,...         % Qph
        surfaceArea_strings,...     % GSA
        chargeInj_strings,...           % Qinj
        maxPotential_strings,...        % Emc
        accessVoltage_strings,...
        voltageDrive_strings,...        % Vdrive
        dateTime_strings,...            % DateTime
        status_strings,...
        'VariableNames',tableHeading);

    % Make filename for .csv file
    %     filename_csv_char = sprintf('%s_Ch%02d.csv',filename_char,channelNum);  % filename for .csv file (char)
    %     filename_csv = convertCharsToStrings(filename_csv_char);             	% filename string for .csv file (string)
    %     filename_csv_save = fullfile(savePath,filename_csv);               	% save path for .csv file

    % Save spreadsheets
    %     writetable(tableData,filename_csv_save);    % save .csv file
    %     fprintf('%s\n',filename_csv);   % .csv file saved
    channelNum_char = sprintf('Channel %d',channelNum);     % channel number (char)
    channelNum_str = convertCharsToStrings(channelNum_char);% channel number (string)
    writetable(tableData,filename_xlsx_save,'Sheet',channelNum_str);    % save sheet to .xlsx file
    channelList_alloc = [channelList,{channelNum_char}];
    channelList = channelList_alloc;
    amplitudeList_alloc = [amplitudeList,amplitude_data];
    amplitudeList = amplitudeList_alloc;
    chargePhaseList_alloc = [chargePhaseList,chargePhase_data];
    chargePhaseList = chargePhaseList_alloc;
    chargeInjList_alloc = [chargeInjList,chargeInj_data];
    chargeInjList = chargeInjList_alloc;
    maxPotentialList_alloc = [maxPotentialList,maxPotential_data(1)];
    maxPotentialList = maxPotentialList_alloc;
    accessVoltageList_alloc = [accessVoltageList,accessVoltage_data(1)];
    accessVoltageList = accessVoltageList_alloc;
    drivingVoltageList_alloc = [drivingVoltageList,voltageDrive_data(1)];
    drivingVoltageList = drivingVoltageList_alloc;
    fprintf('%d...',channelNum);
end
sheetName = 'Values';
headings = tableHeading([4 5 7 8 9 10]);
data_array = [...
    amplitudeList;chargePhaseList;chargeInjList;...
    maxPotentialList;accessVoltageList;drivingVoltageList];
dataTable = array2table(...
    data_array,...
    'VariableNames',channelList,...
    'RowNames',headings);
% data = array2table(...
%     chargeInjList,...
%     maxPotentialList,...
%     drivingVoltageList,...
%     'VariableNames',channelList,...
%     'RowNames',headings);
writetable(dataTable,filename_xlsx_save,'Sheet',sheetName,'WriteRowNames',true);    % save sheet to .xlsx file
fprintf('"%s"\n',filename_xlsx);	% .xlsx file saved
% fprintf('All files saved.\n');% all files saved

end